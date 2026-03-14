from google import genai
from google.genai import types

client = genai.Client(api_key="AIzaSyBg7_183S9hqK60_GVjz5Ej2BMXCZE63x8")

response = client.models.generate_content(
    model="gemini-3-flash-preview",
    contents="How does AI work?",
    config=types.GenerateContentConfig(
        thinking_config=types.ThinkingConfig(thinking_level="low")
    ),
)
print(response.text)