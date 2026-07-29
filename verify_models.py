import google.generativeai as genai
import os
from config import Config

# Configure API
try:
    genai.configure(api_key=Config.GOOGLE_API_KEY)
except AttributeError:
    print("API Key not found in config.py")
    exit(1)

print("Listing available Gemini models:")
try:
    for model in genai.list_models():
        if 'gemini' in model.name:
            print(f"- {model.name} (Supports: {model.supported_generation_methods})")
except Exception as e:
    print(f"Error listing models: {e}")
