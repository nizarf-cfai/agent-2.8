import json
import os
import numpy as np
import pickle
from typing import List
from dotenv import load_dotenv
import time
import google.generativeai as genai
from langchain_community.vectorstores import FAISS
from langchain_core.documents import Document
import os, json, requests
import config
 
# Load env
load_dotenv()
genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))

MODEL = "gemini-2.5-flash-lite"
CACHE_FILE = "embedding_cache.pkl"

BASE_URL = os.getenv("CANVAS_URL", "https://board-v24problem.vercel.app")


def get_board_items():
    url = BASE_URL + "/api/board-items"
    
    response = requests.get(url)
    data = response.json()

    content_object = []
    else_object = []
    for d in data:
        if d.get('id').startswith('raw'):
            pass

        elif d.get('id') == "dashboard-item-chronomed-2":
            d['description'] = "This timeline functions similarly to a medication timeline, but with an expanded DILI assessment focus. It presents a chronological view of the patient’s clinical course, aligning multiple time-bound elements to support hepatotoxicity monitoring. Like the medication timeline tracks periods of drug exposure, this object also visualises medication start/stop dates, dose changes, and hepatotoxic risk levels. In addition, it integrates encounter history, longitudinal liver function test trends, and critical clinical events. Temporal relationships are highlighted to show how changes in medication correlate with laboratory abnormalities and clinical deterioration, providing causality links relevant to DILI analysis. The timeline is designed to facilitate retrospective assessment and ongoing monitoring by showing when key events occurred in relation to medication use and liver injury progression."
            content_object.append(d)


        elif "content"in d.keys() or 'conversationHistory' in d.keys():
            content_object.append(d)

        
        else:
            else_object.append(d)

    with open(f"{config.output_dir}/board_items.json", "w", encoding="utf-8") as f:
        json.dump(content_object, f, indent=4)   # indent=4 makes it pretty
    return content_object

# -----------------------
# Cache helpers
# -----------------------
def load_cache():
    return pickle.load(open(CACHE_FILE, "rb")) if os.path.exists(CACHE_FILE) else {}

def save_cache(cache):
    pickle.dump(cache, open(CACHE_FILE, "wb"))

def get_object_id(obj):
    return obj.get("id") or obj.get("object_id") or str(hash(json.dumps(obj, sort_keys=True)))

# -----------------------
# Embed texts
# -----------------------
def embed_texts(texts: List[str]):
    try:
        res = genai.embed_content(model="models/text-embedding-004", content=texts)

        if "data" in res:
            return [d["embedding"] for d in res["data"]]
        if "embedding" in res:
            emb = res["embedding"]
            return emb if isinstance(emb[0], list) else [emb]
        return []
    except Exception as e:
        print("🚨 Embedding error:", e)
        return []

# -----------------------
# Select relevant objects using cached embeddings
# -----------------------
def rag_object(json_data=[], query: str='', k=10):
    if not json_data:
        json_data = get_board_items()

    cache = load_cache()
    docs, embeddings = [], []
    new_objects = 0

    for obj in json_data:
        obj_id = get_object_id(obj)
        txt = json.dumps(obj, separators=(",", ":"))

        if obj_id in cache:
            emb = cache[obj_id]["embedding"]
        else:
            emb = embed_texts([txt])[0]
            cache[obj_id] = {"embedding": emb, "object": obj}
            new_objects += 1

        docs.append(Document(page_content=txt, metadata=obj))
        embeddings.append(emb)

    if new_objects > 0:
        save_cache(cache)

    print(f"🔹 Total objects: {len(json_data)} | 🆕 Newly embedded: {new_objects} | ⚡ Used cache: {len(json_data) - new_objects}")

    emb_array = np.array(embeddings)
    text_embeddings = [(doc.page_content, emb.tolist()) for doc, emb in zip(docs, emb_array)]

    index = FAISS.from_embeddings(
        text_embeddings=text_embeddings,
        embedding=emb_array
    )

    query_embedding = embed_texts([query])[0]
    search_results = index.similarity_search_by_vector(query_embedding, k=k)
    result_obj = [res.metadata if res.metadata else json.loads(res.page_content) for res in search_results]


    with open(f"faiss_results.json", "w", encoding="utf-8") as f:
        json.dump(result_obj, f, ensure_ascii=False, indent=4)
    return result_obj

# -----------------------
# LLM Answer
# -----------------------
def get_answer(query: str, context=list[dict]):
    with open("system_prompts/chat_model_system.md", "r", encoding="utf-8") as f:
        system_prompt = f.read()

    with open("output/board_items.json", "r", encoding="utf-8") as f:
        json_data = json.load(f)

    if not context:
        retrieved = rag_object(json_data, query)
        context = json.dumps(retrieved, indent=2)

    prompt = f"""
Answer the user query using the given context.

User query: {query}

Context:
{context}
"""

    model = genai.GenerativeModel(MODEL, system_instruction=system_prompt)
    response = model.generate_content(prompt)

    return response.text.strip()


# -----------------------
# Main Execution
# -----------------------
if __name__ == "__main__":
    start_time = time.time()

    query = "tell me summary of sarah miller"
    answer = get_answer(query)

    elapsed = time.time() - start_time

    print("\n🧠 Answer:", answer)
    print(f"⚡ Execution time: {elapsed:.3f} seconds")
