import requests


DOMAIN = "techxagonacademy.com"

HEADERS = {
    "Content-Type": "application/json",
    "X-Server": "chocobo.mxrouting.net",
    "X-Username": "xdmhdejryy",
    "X-API-Key": "Mx130b159b24fed7d73792f3158822K1",
}

email_accounts = [
    {"username": "noelagwulonu", "password": "StrongPass123", "quota": 200, "limit": 100},
    {"username": "tochukwunwabude", "password": "StrongPass124", "quota": 200, "limit": 100},
    {"username": "martinaugwu", "password": "StrongPass125", "quota": 200, "limit": 100},
    {"username": "wisdomenefiok", "password": "StrongPass126", "quota": 200, "limit": 100},
    {"username": "chinazorechezona", "password": "StrongPass127", "quota": 200, "limit": 100},
    {"username": "chinyereokeke", "password": "StrongPass128", "quota": 200, "limit": 100},
    {"username": "abubakarabdulraheem", "password": "StrongPass129", "quota": 200, "limit": 100},
    {"username": "ugwumartina", "password": "StrongPass130", "quota": 200, "limit": 100},
    {"username": "rolandokoye", "password": "StrongPass131", "quota": 200, "limit": 100},
    {"username": "aniemmanuel", "password": "StrongPass132", "quota": 200, "limit": 100},
    {"username": "nyongarchibong", "password": "StrongPass133", "quota": 200, "limit": 100},
    {"username": "ekpomatthew", "password": "StrongPass134", "quota": 200, "limit": 100},
    {"username": "chimegeorge", "password": "StrongPass135", "quota": 200, "limit": 100},
    {"username": "ngumohachukwuemeka", "password": "StrongPass136", "quota": 200, "limit": 100},
    {"username": "onuorahnnamdi", "password": "StrongPass137", "quota": 200, "limit": 100},
    {"username": "madubuikeekene", "password": "StrongPass138", "quota": 200, "limit": 100},
    {"username": "emmanuelokesi", "password": "StrongPass139", "quota": 200, "limit": 100},
    {"username": "ogahpeter", "password": "StrongPass140", "quota": 200, "limit": 100},
    {"username": "fawasraheem", "password": "StrongPass141", "quota": 200, "limit": 100},
    {"username": "abugujamaurice", "password": "StrongPass142", "quota": 200, "limit": 100},
    {"username": "onyedikachinnaji", "password": "StrongPass143", "quota": 200, "limit": 100},
    {"username": "nnamdiiroagbe", "password": "StrongPass144", "quota": 200, "limit": 100},
    {"username": "mercyakintola", "password": "StrongPass145", "quota": 200, "limit": 100},
    {"username": "abdulshaheedabdullahi", "password": "StrongPass146", "quota": 200, "limit": 100},
]

def create_email_account(account):
    url = f"https://api.mxroute.com/domains/{DOMAIN}/email-accounts"

    response = requests.post(
        url,
        headers=HEADERS,
        json=account,
        timeout=30,
    )

    email_address = f"{account['username']}@{DOMAIN}"

    if response.status_code == 201:
        print(f"Created: {email_address}")
        return True

    print(f"Failed: {email_address}")
    print("Status:", response.status_code)
    print("Response:", response.text)
    return False


for account in email_accounts:
    create_email_account(account)