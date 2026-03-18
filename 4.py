

from google import genai
from google.genai import types

client = genai.Client(api_key="AIzaSyBxo48Ee14UpOVy6s4mjLdBhqR-pAJcU4w")

response = client.models.generate_content(
    model="gemini-3-flash-preview",
    contents=["你好"],
    config=types.GenerateContentConfig(
        temperature=0.1
    )
)
print(response.text)