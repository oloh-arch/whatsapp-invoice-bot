import os
import json
import hmac
import hashlib
import base64
import requests
import asyncio
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

# Serve UI
@app.get("/", response_class=HTMLResponse)
async def serve_ui():
    if os.path.exists("index.html"):
        with open("index.html", "r", encoding="utf-8") as f:
            return f.read()
    return "<h1>API is running. index.html is missing in the root directory.</h1>"

# --- 1. AI PARSER ---
def _process_gemini(message: str):
    if not GEMINI_API_KEY:
        return {"error": "GEMINI_API_KEY missing in Render environment variables."}
    
    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
        prompt = f"""
        Extract structured invoice details from this text:
        "{message}"

        Return ONLY a raw JSON object matching this schema:
        {{
          "customer_name": "string",
          "items": [{{"description": "string", "amount": float}}],
          "total_amount": float,
          "due_in_days": int
        }}
        """
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json")
        )
        data = json.loads(response.text)
        
        # Crypto Signature
        raw = f"{data.get('customer_name','')}:{data.get('total_amount',0)}"
        sig = hmac.new(SECRET_KEY.encode('utf-8'), raw.encode('utf-8'), hashlib.sha256).hexdigest()
        data["crypto_signature"] = sig
        
        return {"status": "success", "invoice": data}
    except Exception as e:
        return {"error": f"Gemini Error: {str(e)}"}

@app.post("/parse-invoice")
async def parse_invoice(data: InvoiceRequest):
    result = await asyncio.to_thread(_process_gemini, data.user_message)
    if "error" in result:
        raise HTTPException(status_code=500, detail=result["error"])
    return result

# --- 2. M-PESA STK PUSH ---
def _process_stk(phone: str, amount: float, ref: str):
    if not all([DARAJA_CONSUMER_KEY, DARAJA_CONSUMER_SECRET, DARAJA_PASSKEY]):
        return {
            "status": "mock_success",
            "message": f"Simulated STK Push for {phone} - KES {amount}",
            "CheckoutRequestID": "ws_CO_MOCK_99999"
        }

    try:
        auth_url = "https://sandbox.safaricom.co.ke/oauth/v1/generate?grant_type=client_credentials"
        res = requests.get(auth_url, auth=(DARAJA_CONSUMER_KEY, DARAJA_CONSUMER_SECRET), timeout=10)
        
        if res.status_code != 200:
            return {"error": f"Daraja Auth Failed: {res.text}"}
            
        access_token = res.json().get("access_token")
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
            "Amount": int(amount),
            "PartyA": phone,
            "PartyB": DARAJA_BUSINESS_SHORTCODE,
            "PhoneNumber": phone,
            "CallBackURL": "https://whatsapp-invoice-bot.onrender.com/mpesa-callback",
            "AccountReference": ref,
            "TransactionDesc": "Invoice Payment"
        }

        stk_res = requests.post(
            "https://sandbox.safaricom.co.ke/mpesa/stkpush/v1/processrequest",
            json=stk_payload,
            headers=headers,
            timeout=10
        )
        return stk_res.json()
    except Exception as e:
        return {"error": f"STK Error: {str(e)}"}

@app.post("/stk-push")
async def trigger_stk_push(payload: STKPushRequest):
    result = await asyncio.to_thread(_process_stk, payload.phone_number, payload.amount, payload.account_reference)
    if "error" in result:
        raise HTTPException(status_code=500, detail=result["error"])
    return result

@app.post("/mpesa-callback")
async def mpesa_callback(data: dict):
    return {"ResultCode": 0, "ResultDesc": "Accepted"}
