# rag_tool.py
import os, json, requests
import chromadb
from chromadb.config import Settings
from typing import List
import google.generativeai as genai
from langchain_text_splitters import RecursiveCharacterTextSplitter
from dotenv import load_dotenv
load_dotenv()
import time
import asyncio
import httpx
from typing import Optional
from bs4 import BeautifulSoup
import json, re
import sys
import os
import config

BASE_URL = os.getenv("CANVAS_URL", "https://board-v24problem.vercel.app")

print("#### chroma_script.py CANVAS_URL : ",BASE_URL)

EASL_BASE_URL = "https://inference-dili-16771232505.us-central1.run.app"
genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))

_client: Optional[httpx.AsyncClient] = None


CITE_PATTERN = re.compile(r"\[cite:.*?\]")

def strip_citations(text: str) -> str:
    return CITE_PATTERN.sub("", text).strip()

def parse_kin(text: str):
    # Use XML parser to preserve tag names and whitespace handling
    soup = BeautifulSoup(text, "html.parser")

    short_el = soup.find("short_answer")
    detailed_el = soup.find("detailed_answer")
    refs_el = soup.find("guideline_references")

    result = {
        "short_answer": strip_citations(short_el.get_text(" ", strip=True)) if short_el else None,
        "detailed_answer": strip_citations(detailed_el.get_text(" ", strip=True)) if detailed_el else None,
        "guideline_references": []
    }

    if refs_el:
        inner = refs_el.get_text().strip()

        # First try: treat inner text as a comma-separated sequence of JSON objects -> wrap with []
        try:
            refs = json.loads(f"[{inner}]")
        except json.JSONDecodeError:
            # Fallback: extract each {...} block and parse individually
            refs = []
            for block in re.findall(r"\{.*?\}", inner, flags=re.S):
                try:
                    refs.append(json.loads(block))
                except json.JSONDecodeError:
                    # Soft-clean smart quotes, keep raw on failure
                    cleaned = (block
                               .replace("“", '"').replace("”", '"')
                               .replace("’", "'").replace("‘", "'"))
                    try:
                        refs.append(json.loads(cleaned))
                    except json.JSONDecodeError:
                        refs.append({"raw": block})

        result["guideline_references"] = refs

    return result

def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0))
    return _client


def get_board_items():
    url = BASE_URL + "/api/board-items"
    
    response = requests.get(url)
    data = response.json()


    with open(f"{config.output_dir}/board_items.json", "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)   # indent=4 makes it pretty
    return data


async def start_easl_async(question: str, client: Optional[httpx.AsyncClient] = None) -> str:
    """
    Kick off the EASL job and return task_id.
    Raises httpx.HTTPStatusError on non-2xx.
    """
    client = client or get_client()
    payload = {
        "questions": [{"question": question, "question_id": "q1"}],
        "model_type": "reasoning_model",
    }
    r = await client.post(f"{EASL_BASE_URL}/generate-answers", json=payload)
    r.raise_for_status()
    # API returns a quoted string task id, e.g. "abc123"
    return r.text.strip().strip('"')

async def poll_easl_status(task_id: str, *, interval: float = 3.0, max_retries: int = 5,
                           client: Optional[httpx.AsyncClient] = None) -> bool:
    """
    Poll job status until completed or retries exhausted.
    Returns True if completed, False otherwise.
    """
    client = client or get_client()
    for attempt in range(max_retries + 1):
        r = await client.get(f"{EASL_BASE_URL}/generate-answers/status/{task_id}")
        if r.status_code == 200:
            data = r.json()
            if data.get("status") == "completed":
                return True
        # Optional: small exponential backoff
        await asyncio.sleep(interval * (1.2 ** attempt))
    return False

async def fetch_easl_result(task_id: str, client: Optional[httpx.AsyncClient] = None) -> str:
    """
    Fetch the completed result and return the first LLM answer.
    """
    client = client or get_client()
    r = await client.get(f"{EASL_BASE_URL}/generate-answers/result/{task_id}")
    r.raise_for_status()
    data = r.json()
    return parse_kin(data["results"][0]["llm_answer"])

async def get_easl_answer_async(question: str, *,
                                context: str,
                                interval: float = 60.0,
                                max_retries: int = 5,
                                client: Optional[httpx.AsyncClient] = None) -> str:
    """
    Full async pipeline: start -> poll -> fetch.
    Returns "" if not completed within retries.
    """
    client = client or get_client()


    q_with_context = f"{question}\n\nContext:\n{context}\n"
    
    task_id = await start_easl_async(q_with_context, client=client)
    completed = await poll_easl_status(task_id, interval=interval, max_retries=max_retries, client=client)
    if not completed:
        return ""

    result_answer = await fetch_easl_result(task_id, client=client)
    result_answer['question'] = question
    
    return result_answer


# ----------------------------
# Common embedding helper
# ----------------------------

def embed_texts(texts: List[str]) -> List[List[float]]:
    """Embed texts using Gemini embedding model"""
    model = "models/text-embedding-004"
    try:
        res = genai.embed_content(model=model, content=texts)
        
        
        # Handle the response structure correctly
        if "embedding" in res:
            # Single text input - but we might have multiple chunks
            embedding = res["embedding"]
            # If it's a list of lists (multiple embeddings), return as is
            if isinstance(embedding, list) and len(embedding) > 0 and isinstance(embedding[0], list):
                return embedding
            # If it's a single embedding (list of floats), wrap it
            else:
                return [embedding]
        elif "data" in res:
            # Multiple text inputs
            embeddings = [d["embedding"] for d in res["data"]]
            return embeddings
        else:
            # Fallback - try to extract embeddings from the response
            print(f"Unexpected response structure: {list(res.keys())}")
            return []
    except Exception as e:
        print(f"Error in embed_texts: {e}")
        return []

# ----------------------------
# 1️⃣ Build Chroma from text files
# ----------------------------

def build_chroma_from_texts(dir_path: str, persist_dir: str = "./chroma_store"):
    """Load text files, chunk, and store in Chroma vector DB"""
    client = chromadb.PersistentClient(path=persist_dir)
    collection = client.get_or_create_collection(name="local_docs")

    splitter = RecursiveCharacterTextSplitter(chunk_size=2000, chunk_overlap=200)

    for fname in os.listdir(dir_path):
        if not fname.endswith(".txt"):
            continue
        fpath = os.path.join(dir_path, fname)
        with open(fpath, "r", encoding="utf-8") as f:
            text = f.read()

        chunks = splitter.split_text(text)
        embeddings = embed_texts(chunks)
        ids = [f"{fname}_{i}" for i in range(len(chunks))]

        collection.add(documents=chunks, embeddings=embeddings, ids=ids)
        # print(f"Stored {len(chunks)} chunks from {fname}")

    # print("✅ Chroma built successfully.")
    return collection


# ----------------------------
# 1.5️⃣ Query from persistent ChromaDB collection
# ----------------------------

def query_chroma_collection(query: str, persist_dir: str = "./chroma_store", collection_name: str = "local_docs", top_k: int = 3):

    try:
        # Connect to the persistent ChromaDB
        client = chromadb.PersistentClient(path=persist_dir)
        collection = client.get_collection(name=collection_name)
        
        # Create custom embedding function for queries
        class CustomEmbeddingFunction:
            def __call__(self, input):
                return embed_texts(input)
            
            def embed_query(self, input, **kwargs):
                embeddings = embed_texts([input])
                result = embeddings[0] if embeddings else []
                return [result]
        
        # Set the embedding function for the collection
        collection._embedding_function = CustomEmbeddingFunction()
        
        # Perform the query
        results = collection.query(
            query_texts=[query],
            n_results=top_k
        )
        
        # Extract and return the documents
        if results["documents"] and results["documents"][0]:
            context = "\n".join(results["documents"][0])
            easl_question = f"Question: {query}\nContext: {context}"
            # easl_answer = get_easl_answer(easl_question)
            easl_answer = ""


            rag_res = f"RAG Result: {context}\nEASL Answer: {easl_answer}"

            return rag_res
        else:
            return ""
            
    except Exception as e:
        print(f"Error querying ChromaDB collection: {e}")
        return ""


# ----------------------------
# 2️⃣ RAG from JSON file (no DB)
# ----------------------------
# ---------- Helper: JSON → Markdown ----------

def json_to_markdown(obj: dict, index: int = 0) -> str:
    """
    Convert one JSON object into flat Markdown text.
    - Only a single '# Record {index}' heading.
    - No nested or secondary headings.
    - Nested dicts/lists flattened into simple key paths.
    """
    except_keys = ['x', 'y', 'width', 'height','color']
    lines = [f"# Object Record {index}"]

    def flatten(prefix, value):
        if isinstance(value, dict):
            for k, v in value.items():
                new_key = f"{prefix}_{k}" if prefix else k
                flatten(new_key, v)
        elif isinstance(value, list):
            for i, v in enumerate(value):
                new_key = f"{prefix}_{i}" if prefix else f"{prefix}_{i}"
                flatten(new_key, v)
        else:
            key_clean = prefix.replace("_", " ")
            if key_clean in except_keys:
                pass
            elif key_clean == 'id':
                key_clean = 'objectId'
                lines.append(f"**{key_clean}:** {value}")
            else:
                lines.append(f"**{key_clean}:** {value}")

    flatten("", obj)
    return "\n".join(lines)


async def block_rag(str_list: list = [], query: str="", top_k: int = 3):
    
    # Create in-memory Chroma collection with custom embedding function
    client = chromadb.Client(Settings(anonymized_telemetry=False))
    
    # Create a unique collection name based on JSON path and timestamp
    collection_name = f"temp_block_rag_rag"
    
    try:
        # Create a custom embedding function for queries
        class CustomEmbeddingFunction:
            def __call__(self, input):
                return embed_texts(input)
            
            def embed_query(self, input, **kwargs):
                embeddings = embed_texts([input])
                result = embeddings[0] if embeddings else []
                # ChromaDB expects a list of lists even for single query
                return [result]
        
        # Try to delete existing collection if it exists, then create new one
        try:
            client.delete_collection(name=collection_name)
        except:
            pass  # Collection doesn't exist, which is fine
        
        collection = client.create_collection(
            name=collection_name,
            embedding_function=CustomEmbeddingFunction()
        )

        # Use each md_block as a single chunk to preserve objectId integrity
        chunks = str_list

        # Embed and store in Chroma
        embeddings = embed_texts(chunks)
        ids = [f"chunk_{i}" for i in range(len(chunks))]
                
        # Save original stdout and stderr
        original_stdout = sys.stdout
        original_stderr = sys.stderr
        
        # Redirect to devnull
        with open(os.devnull, 'w') as devnull:
            sys.stdout = devnull
            sys.stderr = devnull
            try:
                collection.add(documents=chunks, embeddings=embeddings, ids=ids)
            finally:
                # Restore original stdout and stderr
                sys.stdout = original_stdout
                sys.stderr = original_stderr

        # Query
        results = collection.query(query_texts=[query], n_results=top_k)

        context = "\n\n".join(results["documents"][0])

        return context
        
    finally:
        # Clean up: delete the temporary collection
        try:
            client.delete_collection(name=collection_name)
        except:
            pass  # Collection might already be deleted, which is fine


async def rag_from_json(query: str="", top_k: int = 10):

    
    try:

        data = get_board_items()
        summary_objects = []
        raw_objects = []
        for d in data:
            if 'raw' in d.get('id',''):
                raw_objects.append(d)
            elif 'single-encounter' in d.get('id',''):
                raw_objects.append(d)
            elif 'iframe' in d.get('id',''):
                raw_objects.append(d)
            else:
                summary_objects.append(d)

        print("Summary object len:", len(summary_objects))
        print("Raw object len:", len(raw_objects))

        # Convert each object to Markdown string
        summary_objects_blocks = [json_to_markdown(obj, i) for i,obj in enumerate(summary_objects)]
        raw_objects_blocks = [json_to_markdown(obj, i) for i,obj in enumerate(raw_objects)]

        summary_res = await block_rag(summary_objects_blocks,query,top_k=3)
        
        raw_res = await block_rag(raw_objects_blocks,query,top_k=6)


        # context = "\n\n".join(summary_result[:3]) + "\n\n".join(raw_result[:3])
        # context = "\n\n".join(results["documents"][0])
        context = summary_res + "\n\n" + raw_res
        with open(f"{config.output_dir}/rag_result.md", "w", encoding="utf-8") as f:
            f.write(context)
        return context
        
    except Exception as e:
        print(f"Error object_rag :\n{e}")
        return ""

