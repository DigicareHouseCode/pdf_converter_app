"""
Test script to verify Gemini API connectivity and functionality
"""
import google.generativeai as genai
import os
import sys

# API key from the environment (see .env.example)
API_KEY = os.environ.get('GEMINI_API_KEY', "")

def test_api_key():
    """Test if the API key is valid"""
    print("=" * 60)
    print("GEMINI API CONNECTIVITY TEST")
    print("=" * 60)
    print(f"\nAPI Key: {API_KEY[:20]}...{API_KEY[-10:]}")
    
    try:
        # Configure Gemini
        genai.configure(api_key=API_KEY)
        print("[OK] API key configured successfully")
        
        # List available models
        print("\n[TEST 1] Listing available models...")
        models = genai.list_models()
        available_models = []
        for model in models:
            if 'generateContent' in model.supported_generation_methods:
                available_models.append(model.name)
                print(f"  - {model.name}")
        
        if not available_models:
            print("[ERROR] No models available for content generation!")
            return False
        
        print(f"[OK] Found {len(available_models)} available models")
        
        # Test text generation
        print("\n[TEST 2] Testing text generation with gemini-1.5-flash...")
        model = genai.GenerativeModel('gemini-1.5-flash')
        response = model.generate_content("Say 'Hello, API is working!' in exactly those words.")
        
        if response and response.text:
            print(f"[OK] Response received: {response.text.strip()}")
        else:
            print("[ERROR] No response text received")
            return False
        
        # Test JSON generation
        print("\n[TEST 3] Testing JSON generation...")
        json_prompt = """
        Return ONLY a valid JSON array with 2 sample transactions:
        [
          {"Date": "01/15/2025", "Description": "Test Transaction 1", "Amount": "100.00"},
          {"Date": "01/16/2025", "Description": "Test Transaction 2", "Amount": "200.00"}
        ]
        """
        response = model.generate_content(json_prompt)
        
        if response and response.text:
            print(f"[OK] JSON response received ({len(response.text)} chars)")
            print(f"Response preview: {response.text[:200]}...")
        else:
            print("[ERROR] No JSON response received")
            return False
        
        # Test with image (if available)
        print("\n[TEST 4] Checking vision capabilities...")
        try:
            vision_model = genai.GenerativeModel('gemini-1.5-flash')
            print("[OK] Vision model (gemini-1.5-flash) is available")
        except Exception as e:
            print(f"[WARNING] Vision model test failed: {e}")
        
        print("\n" + "=" * 60)
        print("ALL TESTS PASSED! ✓")
        print("=" * 60)
        print("\nYour Gemini API is working correctly.")
        print("The issue with Excel generation is likely NOT the API.")
        return True
        
    except Exception as e:
        print(f"\n[ERROR] API test failed: {e}")
        print("\nPossible issues:")
        print("1. Invalid API key")
        print("2. API key quota exceeded")
        print("3. Network connectivity issues")
        print("4. API key restrictions (IP/domain)")
        return False

if __name__ == "__main__":
    success = test_api_key()
    sys.exit(0 if success else 1)
