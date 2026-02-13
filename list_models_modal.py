import modal

image = modal.Image.debian_slim(python_version="3.12").pip_install("google-genai")
app = modal.App("list-models", image=image)

@app.function(secrets=[modal.Secret.from_name("ea-secrets")])
def list_imagen_models():
    import os
    from google import genai
    
    print("Listing models...")
    try:
        client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
        found = False
        for model in client.models.list(config={"page_size": 100}):
            if "imagen" in model.name.lower():
                print(f"Found model: {model.name}")
                found = True
        if not found:
            print("No models with 'imagen' in the name found.")
    except Exception as e:
        print(f"Error listing models: {e}")

