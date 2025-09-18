from texagonbackend.settings import PAYMENT_TEST ,TEST_KEY_SECRET,FLW_SECRET_KEY, LOGO_URL
import requests
from django.contrib.sites.shortcuts import get_current_site

def get_payment_link(payload):
    if PAYMENT_TEST:
        FLW_SECRET_KEY = TEST_KEY_SECRET
        
    url = "https://api.flutterwave.com/v3/payments"

    headers = {
        "Authorization": f"Bearer {FLW_SECRET_KEY}"
    }
    #{'status': 'success', 'message': 'Hosted Link', 'data': 
    #{'link': 'https://ravemodal-dev.herokuapp.com/v3/hosted/pay/743317355dda6b531839'}}
    try:
        response = requests.post(url, headers=headers, json=payload)
        response.raise_for_status()
        json_response = response.json()
        if json_response['status'] == 'success':
            return json_response['data']['link']
        return False
    except requests.exceptions.HTTPError as errh:
        print(f"HTTP Error: {errh}")
        return False
    except requests.exceptions.RequestException as err:
        #print(f"Request Exception: {err}")
        return False


def generate_payment_link(request, user_id:int, tx_ref:str,redirect_url:str,title:str,customer_detail:dict,total_amount:float, payment_plan:str):
    if '127.0.0.1:8000' in get_current_site(request).domain:
        redirect_url= f"http://127.0.0.1:8000{redirect_url}"
    else:
        redirect_url = f"https://{get_current_site(request).domain}{redirect_url}"

    payload = {
        "tx_ref": tx_ref,
        "amount": f"{total_amount}",
        "currency": "NGN",
        "payment_options":"card",
        "redirect_url": redirect_url,
        "meta": {
            "consumer_id": f"{user_id}",
            "payment_plan":payment_plan,
        },
        "customer": customer_detail,
        "customizations": {
            "title": title,
            "logo": LOGO_URL
        }
    }

    return get_payment_link(payload)

def confirm_transaction(transaction_id):
    
    if PAYMENT_TEST:
        FLW_SECRET_KEY = TEST_KEY_SECRET
    #TEST_KEY_SECRET = "FLWSECK_TEST-baf077665388f439db111cb3d2a94181-X"
    url = f"https://api.flutterwave.com/v3/transactions/{transaction_id}/verify"
    headers = {'Content-Type': 'application/json','Authorization':f'Bearer {FLW_SECRET_KEY}'}
    r = requests.get(url, headers=headers)
    r = r.json()
    status = r['status']
    return (r,status)