import os
import json
import hmac
import hashlib
import base64
import requests
from datetime import datetime
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from google import genai
from google.genai import types

app = FastAPI(title="WhatsApp AI Invoice & M-Pesa Bot")

# Environment Variables
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
SECRET_KEY = os.getenv("SECRET_KEY", "super-secret-key-12345")
DARAJA_CONSUMER_KEY = os.getenv("DARAJA_CONSUMER_KEY")
DARAJA_CONSUMER_SECRET = os.getenv("DARAJA_CONSUMER_SECRET")
DARAJA_PASSKEY = os.getenv("DARAJA_PASSKEY")
DARAJA_BUSINESS_SHORTCODE = os.getenv("DARAJA_BUSINESS_SHORTCODE", "174379")

class InvoiceRequest(BaseModel):
    user_message: str

class STKPushRequest(BaseModel):
    phone_number: str
    amount: float
    account_reference: str

# Serve frontend UI directly on root URL
@app.get("/", response_class=HTMLResponse)
def serve_ui():
    if os.path.exists("index.html"):
        with open("index.html", "r", encoding="utf-8") as f:
            return f.read()
    return "<h1>API is running, but index.html was not found in root directory.</h1>"

# --- 1. AI PARSER & CRYPTOGRAPHIC SIGNATURE ---
@app.post("/parse-invoice")
def parse_invoice(data: InvoiceRequest):
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
        
        # Cryptographic Signature
        raw_payload = f"{parsed_data.get('customer_name','')}:{parsed_data.get('total_amount',0)}"
        signature = hmac.new(
            SECRET_KEY.encode('utf-8'),
            raw_payload.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()

        parsed_data["crypto_signature"] = signature
        return {"status": "success", "invoice": parsed_data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# --- 2. SAFARICOM DARAJA M-PESA STK PUSH ---
def get_daraja_access_token():
    url = "https://sandbox.safaricom.co.ke/oauth/v1/generate?grant_type=client_credentials"
    response = requests.get(url, auth=(DARAJA_CONSUMER_KEY, DARAJA_CONSUMER_SECRET))
    if response.status_code == 200:
        return response.json().get("access_token")
    raise HTTPException(status_code=400, detail="Failed to fetch Daraja Access Token")

@app.post("/stk-push")
def trigger_stk_push(payload: STKPushRequest):
    if not all([DARAJA_CONSUMER_KEY, DARAJA_CONSUMER_SECRET, DARAJA_PASSKEY]):
        return {
            "status": "mock_success",
            "message": f"STK Push simulated for {payload.phone_number} - KES {payload.amount}",
            "CheckoutRequestID": "ws_CO_MOCK_123456789"
        }

    access_token = get_daraja_access_token()
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    password = base64.b64encode(
        f"{DARAJA_BUSINESS_SHORTCODE}{DARAJA_PASSKEY}{timestamp}".encode("utf-8")
    ).decode("utf-8")

    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
    
    stk_payload = {
        "BusinessShortCode": DARAJA_BUSINESS_SHORTCODE,
        "Password": password,
        "Timestamp": timestamp,
        "TransactionType": "CustomerPayBillOnline",
        "Amount": int(payload.amount),
        "PartyA": payload.phone_number,
        "PartyB": DARAJA_BUSINESS_SHORTCODE,
        "PhoneNumber": payload.phone_number,
        "CallBackURL": "https://whatsapp-invoice-bot.onrender.com/mpesa-callback",
        "AccountReference": payload.account_reference,
        "TransactionDesc": "Invoice Payment"
    }

    response = requests.post(
        "https://sandbox.safaricom.co.ke/mpesa/stkpush/v1/processrequest",
        json=stk_payload,
        headers=headers
    )
    return response.json()

@app.post("/mpesa-callback")
def mpesa_callback(data: dict):
    print("M-Pesa Callback Received:", data)
    return {"ResultCode": 0, "ResultDesc": "Accepted"}
