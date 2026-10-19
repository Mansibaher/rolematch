# Models

`all-MiniLM-L6-v2/` holds the sentence-embedding model used for semantic matching (config.json,
tokenizer.json, model.safetensors). Weights are not committed. Fetch the official files with
`python backend/scripts/fetch_model.py` (Hugging Face: sentence-transformers/all-MiniLM-L6-v2, Apache-2.0).

The original build environment could not reach Hugging Face, so its evaluation used an fp16 copy of the same
weights from the npm package `@lat.md/embed-minilm-fp16` (Apache-2.0); RoleMatch loads either
`model.safetensors` or `model.fp16.safetensors`.
