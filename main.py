from openai import OpenAI
import base64
from pathlib import Path
import os
from dotenv import load_dotenv

load_dotenv()

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

def chat_with_gpt(prompt: str, image_path: str | None = None) -> str:
    content = []

    if prompt:
        content.append({
            "type": "input_text",   
            "text": prompt
        })

    if image_path:
        image_bytes = Path(image_path).read_bytes()
        image_b64 = base64.b64encode(image_bytes).decode("utf-8")
        
        content.append({
            "type": "input_image",
            "image_url": f"data:image/png;base64,{image_b64}"
        })

    response = client.responses.create(
        model="gpt-4.1-mini",
        input=[
            {
                "role": "user",
                "content": content
            }
        ]
    )

    return response.output_text.strip()

if __name__ == "__main__":
    while True:
        user_input = input("You: ")
        if user_input.lower() in ["quit", "exit", "bye"]:
            break

        response = chat_with_gpt(user_input)
        print ("Chatbot: ", response)