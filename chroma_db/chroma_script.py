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
import uuid 
import numpy as np
import hashlib  # ✅ Added for caching

# ---------------------------------------------------------
# 📦 IMPORTS FOR RAG (FAISS + Gemini Embeddings)
# ---------------------------------------------------------
with open("object_desc.json", "r", encoding="utf-8") as f:
    object_desc = json.load(f)
object_desc_data = {}
existing_desc_ids = []
for o in object_desc:
    object_desc_data[o['id']] = o['description']
    existing_desc_ids.append(o['id'])
# 1. FAISS for Vector Search
try:
    import faiss
except ImportError:
    print("⚠️ FAISS not found. Please install it: pip install faiss-cpu")
    faiss = None

# 2. Configure Gemini
if os.getenv("GOOGLE_API_KEY"):
    genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))
else:
    print("⚠️ GOOGLE_API_KEY not found. Gemini embeddings will fail.")

BASE_URL = os.getenv("CANVAS_URL", "https://boardv28.vercel.app")
print("#### chroma_script.py CANVAS_URL : ", BASE_URL)


def get_board_items():
    url = BASE_URL + "/api/board-items"
    data = []
    
    # 1. Try fetching from API
    try:
        print(f"🌍 Fetching from: {url}")
        response = requests.get(url, timeout=5)
        
        if response.status_code == 200:
            try:
                data = response.json()
                # Save to cache
                os.makedirs(config.output_dir, exist_ok=True)
                with open(f"{config.output_dir}/board_items.json", "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=4)
                print(f"✅ Fetched {len(data)} items from API")
                return data
            except json.JSONDecodeError:
                print(f"❌ Invalid JSON response from API. Text: {response.text[:100]}...")
        else:
            print(f"⚠️ API Error: Status {response.status_code}")
            
    except Exception as e:
        print(f"⚠️ API Connection failed: {e}")

        # 2. Fallback to local file
        local_path = f"{config.output_dir}/board_items.json"
        if os.path.exists(local_path):
            print(f"📂 Falling back to local cache: {local_path}")
            try:
                with open(local_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    print(f"✅ Loaded {len(data)} items from cache")
                    return data
            except Exception as e:
                print(f"❌ Failed to load local cache: {e}")
            
    return []

def embed_texts(texts: List[str]) -> List[List[float]]:
    """Embed texts using Gemini API (No local model loading)"""
    model = "models/text-embedding-004"
    if not texts:
        return []
        
    try:
        # Gemini batch embedding
        res = genai.embed_content(model=model, content=texts)
        
        if "embedding" in res:
            embedding = res["embedding"]
            if isinstance(embedding, list) and len(embedding) > 0 and isinstance(embedding[0], list):
                return embedding
            else:
                return [embedding]
        elif "data" in res:
            embeddings = [d["embedding"] for d in res["data"]]
            return embeddings
        else:
            print(f"Unexpected response structure: {list(res.keys())}")
            return []
    except Exception as e:
        print(f"Error in embed_texts: {e}")
        return []


# ----------------------------
# 2️⃣ RAG from JSON file (FAISS + Gemini Embeddings + Caching)
# ----------------------------

def json_to_markdown(obj: dict, index: int = 0) -> str:
    lines = [f"# Record {index}"]
    
    # 1. Keys to completely ignore (Metadata & UI props)
    ignore_keys = {
        'x', 'y', 'width', 'height', 'color', 
        'type', 'componentType', 
        'component', # Inside content
        'createdAt', 'updatedAt', 
        'rotation', 'conversationHistory', 'showHandles', 'handlePosition', 'draggable', 'selectable', 'zIndex',
        'buttonText', 'buttonIcon', 'buttonColor', 'buttonAction', 'iframeUrl'
    }
    
    # 2. Path segments to skip (to shorten the keys)
    # e.g. "content props patientData name" -> "name"
    skip_segments = {'content', 'props', 'patientData', 'data'}

    def flatten(prefix, value):
        if isinstance(value, dict):
            for k, v in value.items():
                if k in ignore_keys:
                    continue
                
                # Skip unnecessary nesting names
                if k in skip_segments:
                    new_prefix = prefix
                else:
                    new_prefix = f"{prefix} {k}" if prefix else k
                    
                flatten(new_prefix, v)
        elif isinstance(value, list):
            for i, v in enumerate(value):
                # Use 1-based indexing for readability
                new_prefix = f"{prefix} {i+1}" if prefix else str(i+1)
                flatten(new_prefix, v)
        else:
            # Final value cleanup
            key = prefix.strip()
            if not key: return
            lines.append(f"**{key}:** {value}")

    flatten("", obj)
    return "\n".join(lines)

def _block_rag_sync(str_list, query, top_k):
    """Synchronous worker for FAISS operations with Caching"""
    if not str_list:
        return ""
        
    if faiss is None:
        print("❌ FAISS is not installed. Cannot perform search.")
        return ""

    try:
        # -------------------------------------------------
        # ⚡ CACHING LOGIC
        # -------------------------------------------------
        # 1. Calculate Hash of the content
        # We join all strings to create a unique signature for this dataset
        content_str = "".join(str_list)
        content_hash = hashlib.md5(content_str.encode("utf-8")).hexdigest()
        
        # 2. Define Cache Path
        cache_dir = os.path.join(config.output_dir, "embeddings_cache")
        os.makedirs(cache_dir, exist_ok=True)
        cache_file = os.path.join(cache_dir, f"emb_{content_hash}.npy")
        
        xb = None # The embeddings array
        
        # 3. Try to Load from Cache
        if os.path.exists(cache_file):
            try:
                # print(f"⚡ Loading cached embeddings: {cache_file}")
                xb = np.load(cache_file)
            except Exception as e:
                print(f"⚠️ Cache load failed, regenerating: {e}")
                xb = None
        
        # 4. Generate if not cached (or load failed)
        if xb is None:
            print(f"🧬 Data changed or no cache. Generating new embeddings via Gemini...")
            embeddings_list = embed_texts(str_list)
            
            if not embeddings_list:
                return ""
                
            # Convert to numpy array (float32 is required by FAISS)
            xb = np.array(embeddings_list).astype('float32')
            
            # Save to cache for next time
            try:
                np.save(cache_file, xb)
                print(f"💾 Embeddings saved to cache: {cache_file}")
            except Exception as e:
                print(f"⚠️ Failed to save cache: {e}")

        # -------------------------------------------------
        # 🔍 FAISS SEARCH
        # -------------------------------------------------
        
        d = xb.shape[1] # Dimension
        
        # IndexFlatIP (Inner Product) = Cosine Similarity if vectors are normalized
        index = faiss.IndexFlatIP(d) 
        
        # Normalize vectors for Cosine Similarity
        faiss.normalize_L2(xb)
        index.add(xb)
        
        # Generate Embedding for Query (Always needs to be generated)
        query_embedding = embed_texts([query])
        if not query_embedding:
            return ""
            
        xq = np.array(query_embedding).astype('float32')
        faiss.normalize_L2(xq)
        
        # Search
        D, I = index.search(xq, top_k)
        
        # Retrieve Results
        results = []
        for idx in I[0]:
            if idx != -1 and idx < len(str_list):
                results.append(str_list[idx])
                
        return "\n\n".join(results)
        
    except Exception as e:
        print(f"Error in FAISS search: {e}")
        return ""

async def block_rag(str_list: list = [], query: str="", top_k: int = 3):
    if not str_list:
        return ""
    # Run the blocking sync function in a separate thread
    return await asyncio.to_thread(_block_rag_sync, str_list, query, top_k)

async def rag_from_json(query: str="", top_k: int = 10):
    try:
        data = get_board_items()
        summary_objects = []
        raw_objects = []
        
        for d in data:
            if not d: continue
            d_id = d.get('id', '')
            if 'raw' in d_id or 'single-encounter' in d_id or 'iframe' in d_id:
                raw_objects.append(d)
            elif d.get('id') == "dashboard-item-chronomed-2":
                d['description'] = "This timeline functions similarly to a medication timeline, but with an expanded DILI assessment focus. It presents a chronological view of the patient’s clinical course, aligning multiple time-bound elements to support hepatotoxicity monitoring. Like the medication timeline tracks periods of drug exposure, this object also visualises medication start/stop dates, dose changes, and hepatotoxic risk levels. In addition, it integrates encounter history, longitudinal liver function test trends, and critical clinical events. Temporal relationships are highlighted to show how changes in medication correlate with laboratory abnormalities and clinical deterioration, providing causality links relevant to DILI analysis. The timeline is designed to facilitate retrospective assessment and ongoing monitoring by showing when key events occurred in relation to medication use and liver injury progression."
                summary_objects.append(d)
            elif 'dashboard-item' in d_id:
                if d.get('type') == 'component':
                    if d.get('id') in existing_desc_ids:
                        d['description'] = object_desc_data.get(d.get('id'), '')
                    summary_objects.append(d)
            else:
                pass

        # print(f"Summary objects: {len(summary_objects)}, Raw objects: {len(raw_objects)}")

        # Convert to Markdown
        summary_objects_blocks = [json_to_markdown(obj, i) for i,obj in enumerate(summary_objects)]
        raw_objects_blocks = [json_to_markdown(obj, i) for i,obj in enumerate(raw_objects)]

        # Run in parallel threads
        t1 = asyncio.create_task(block_rag(summary_objects_blocks, query, top_k=3))
        t2 = asyncio.create_task(block_rag(raw_objects_blocks, query, top_k=7))
        
        summary_res, raw_res = await asyncio.gather(t1, t2)

        context = summary_res + "\n\n" + raw_res
        
        with open(f"{config.output_dir}/rag_result.md", "w", encoding="utf-8") as f:
            f.write(context)
        return context
        
    except Exception as e:
        print(f"Error object_rag :\n{e}")
        return ""