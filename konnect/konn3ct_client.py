import requests


class Konn3ctAPI:
    def __init__(self, token: str, base_url="https://dev.konn3ct.ng/api"):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        })

    # ---------------------------------------------------
    # 1. CREATE ROOM
    # ---------------------------------------------------
    def create_room(self, name, logout_url="", access_code="", welcome_message=""):
        url = f"{self.base_url}/create-room"

        payload = {
            "name": name,
            "logout_url": logout_url,
            "access_code": access_code,
            "welcome_message": welcome_message
        }

        r = self.session.post(url, json=payload)
        r.raise_for_status()
        return r.json()

    # ---------------------------------------------------
    # 2. START ROOM
    # ---------------------------------------------------
    def start_room(self, room_id, name, started_by,
                   logout_url="", message="", keyword="", access_code=""):

        url = f"{self.base_url}/start-room"

        payload = {
            "id": room_id,
            "name": name,
            "logout_url": logout_url,
            "message": message,
            "started_by": started_by,
            "keyword": keyword,
            "access_code": access_code
        }

        r = self.session.post(url, json=payload)
        r.raise_for_status()
        return r.json()

    # ---------------------------------------------------
    # 3. JOIN ROOM
    # ---------------------------------------------------
    def join_room(self, room_id, name, email, role="moderator", access_code=""):
        url = f"{self.base_url}/join-room"

        payload = {
            "id": room_id,
            "name": name,
            "email": email,
            "role": role,
            "access_code": access_code
        }

        r = self.session.post(url, json=payload)
        r.raise_for_status()
        return r.json()

    # ---------------------------------------------------
    # Optional Helpers
    # ---------------------------------------------------
    def room_status(self, room_id):
        return self.session.get(f"{self.base_url}/room-status/{room_id}").json()

    def list_rooms(self):
        return self.session.get(f"{self.base_url}/list-rooms").json()