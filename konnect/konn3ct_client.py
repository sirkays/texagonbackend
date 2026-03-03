import requests
from typing import Iterable, List, Dict, Any, Optional, Union


class Konn3ctAPI:
    """
    Full client implementing endpoints from the Konn3ct Postman collection.
    See the collection you uploaded for example request/response bodies. :contentReference[oaicite:1]{index=1}
    """

    def __init__(self, token: str, base_url: str = "https://dev.konn3ct.ng/api", timeout: int = 10):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        })
        self.timeout = timeout

    # ---------------------------
    # Helpers
    # ---------------------------
    @staticmethod
    def _normalize_list_of_items(items: Optional[Iterable]) -> List[Dict[str, Any]]:
        """
        Accepts:
          - None -> []
          - iterable of dicts -> return list(dict)
          - iterable of ints/str -> convert to [{"id": int_or_str}, ...]
        """
        if not items:
            return []
        normalized = []
        for it in items:
            if isinstance(it, dict):
                normalized.append(it)
            else:
                normalized.append({"id": it})
        return normalized

    def _get(self, path: str, params: Optional[dict] = None) -> Dict[str, Any]:
        url = f"{self.base_url}/{path.lstrip('/')}"
        r = self.session.get(url, params=params or {}, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, payload: Optional[dict] = None) -> Dict[str, Any]:
        url = f"{self.base_url}/{path.lstrip('/')}"
        r = self.session.post(url, json=payload or {}, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def _delete(self, path: str) -> Dict[str, Any]:
        url = f"{self.base_url}/{path.lstrip('/')}"
        r = self.session.delete(url, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    # ---------------------------
    # 1. Create Room
    # POST /create-room
    # ---------------------------
    def create_room(
        self,
        name: str,
        logout_url: str = "",
        access_code: str = "",
        welcome_message: str = "",
        allowed_courses: Optional[Iterable] = None,
        allowed_users: Optional[Iterable] = None,
    ) -> Dict[str, Any]:
        """
        Create a room; optional canonical allowed lists can be provided.
        """
        payload = {
            "name": name,
            "logout_url": logout_url,
            "access_code": access_code,
            "welcome_message": welcome_message,
            "allowed_courses": self._normalize_list_of_items(allowed_courses),
            "allowed_users": self._normalize_list_of_items(allowed_users),
        }
        return self._post("create-room", payload)

    # ---------------------------
    # 2. List Rooms
    # GET /list-rooms
    # ---------------------------
    def list_rooms(self, params: Optional[dict] = None) -> Dict[str, Any]:
        """
        Returns the list of rooms (example response: success,message,data[list])
        """
        return self._get("list-rooms", params=params)

    # ---------------------------
    # 3. List Rooms With Status
    # GET /list-rooms-withstatus
    # ---------------------------
    def list_rooms_with_status(self, params: Optional[dict] = None) -> Dict[str, Any]:
        return self._get("list-rooms-withstatus", params=params)

    # ---------------------------
    # 4. Delete Room
    # DELETE /delete-room/{id}
    # ---------------------------
    def delete_room(self, room_id: Union[int, str]) -> Dict[str, Any]:
        return self._delete(f"delete-room/{room_id}")

    # ---------------------------
    # 5. Start Room
    # POST /start-room
    # ---------------------------
    def start_room(
        self,
        room_id: Union[int, str],
        name: str,
        started_by: str,
        logout_url: str = "",
        message: str = "",
        keyword: str = "",
        access_code: str = "",
        allowed_courses: Optional[Iterable] = None,
        allowed_users: Optional[Iterable] = None,
    ) -> Dict[str, Any]:
        """
        Start an existing room. The Postman examples send:
        {
          "id": 88,
          "name": "...",
          "logout_url": "...",
          "message": "...",
          "started_by": "...",
          "keyword": "...",
          "access_code": "..."
        }
        We add optional allowed_courses and allowed_users to match your Django flow.
        """
        payload = {
            "id": room_id,
            "name": name,
            "logout_url": logout_url,
            "message": message,
            "started_by": started_by,
            "keyword": keyword,
            "access_code": access_code,
            "allowed_courses": self._normalize_list_of_items(allowed_courses),
            "allowed_users": self._normalize_list_of_items(allowed_users),
        }
        return self._post("start-room", payload)

    # ---------------------------
    # 6. Join Room
    # POST /join-room
    # ---------------------------
    def join_room(self, room_id: Union[int, str], name: str, email: str, role: str = "moderator", access_code: str = "") -> Dict[str, Any]:
        payload = {
            "id": room_id,
            "name": name,
            "email": email,
            "role": role,
            "access_code": access_code
        }
        return self._post("join-room", payload)

    # ---------------------------
    # 7. Meeting Info / Validate Meeting Name
    # POST /meeting-info
    # ---------------------------
    def meeting_info(self, name: str) -> Dict[str, Any]:
        """
        Validate meeting name (Postman example posts {"name": "tesss"}).
        """
        return self._post("meeting-info", {"name": name})

    # ---------------------------
    # 8. Room Status (meeting status)
    # GET /room-status/{id}
    # ---------------------------
    def room_status(self, room_id: Union[int, str]) -> Dict[str, Any]:
        """
        Returns whether the room is active and optionally participants.
        """
        return self._get(f"room-status/{room_id}")

    # ---------------------------
    # 9. Room Details
    # GET /room-details/{id}
    # ---------------------------
    def room_details(self, room_id: Union[int, str]) -> Dict[str, Any]:
        return self._get(f"room-details/{room_id}")

    # ---------------------------
    # 10. List Room Recordings
    # (endpoint name in collection: List Room Recording)
    # GET /list-room-recording or similar (try both common variants)
    # ---------------------------
    def list_room_recordings(self, room_id: Union[int, str]) -> Dict[str, Any]:
        """
        The Postman file contains a 'List Room Recording' endpoint — if the actual route differs,
        change this path to the correct one (e.g., 'list-room-recording/{id}' or 'list-recordings/{id}').
        """
        # Try common variants (best-effort)
        for path in (f"list-room-recording/{room_id}", f"list-recordings/{room_id}", f"list-recording/{room_id}"):
            try:
                return self._get(path)
            except requests.HTTPError:
                # try next candidate
                continue
        # If none succeeded, raise a clear error
        raise requests.HTTPError("list_room_recordings: no known recording endpoint succeeded for room_id=" + str(room_id))

    # ---------------------------
    # 11. List Attendance
    # GET /list-attendance/{id}
    # ---------------------------
    def list_attendance(self, meeting_id: Union[int, str]) -> Dict[str, Any]:
        return self._get(f"list-attendance/{meeting_id}")

    # ---------------------------
    # 12. Meeting History / List History
    # GET /list-history or similar
    # ---------------------------
    def list_history(self, params: Optional[dict] = None) -> Dict[str, Any]:
        """
        Returns meeting history. Postman contains a meeting history / meeting logs endpoint.
        Use params to filter by meeting id if supported.
        """
        # Try common variants
        for path in ("list-history", "list-meeting-history", "meeting-history"):
            try:
                return self._get(path, params=params)
            except requests.HTTPError:
                continue
        raise requests.HTTPError("list_history: no history endpoint succeeded.")

    # ---------------------------
    # 13. Convenience: list_attendance_by_room (alias)
    # ---------------------------
    def list_attendance_by_room(self, room_id: Union[int, str]) -> Dict[str, Any]:
        # Some APIs use meeting_id == room_id, so reuse method
        return self.list_attendance(room_id)

    # ---------------------------
    # 14. Generic raw access (for any undocumented endpoint)
    # ---------------------------
    def raw_get(self, path: str, params: Optional[dict] = None) -> Dict[str, Any]:
        return self._get(path, params=params)

    def raw_post(self, path: str, payload: Optional[dict] = None) -> Dict[str, Any]:
        return self._post(path, payload=payload)

    def raw_delete(self, path: str) -> Dict[str, Any]:
        return self._delete(path)