import os
import json
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from google import genai
from google.genai import types

app = FastAPI(title="WhatsApp AI Invoice Generator")

# Initialize Gemini Client using environment variable
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

class InvoiceRequest(BaseModel):
    user_message: str

@app.get("/")
def home():
    return {"status": "online", "message": "WhatsApp Invoice Bot is running!"}

@app.post("/parse-invoice")
def parse_invoice(data: InvoiceRequest):
    """
    Parses natural language invoice text into structured JSON using Gemini 2.5 Flash.
    """
    if not GEMINI_API_KEY:
        raise HTTPException(status_code=500, detail="GEMINI_API_KEY environment variable is missing.")

    client = genai.Client(api_key=GEMINI_API_KEY)

    prompt = f"""
    Extract structured invoice details from this text message:
    "{data.user_message}"

    Return ONLY a raw JSON object matching this exact schema:
    {{
      "customer_name": "string",
      "items": [
        {{"description": "string", "amount": float}}
      ],
      "total_amount": float,
      "due_in_days": int
    }}
    """
    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json")
        )
        parsed_data = json.loads(response.text)
        return {"status": "success", "invoice": parsed_data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
