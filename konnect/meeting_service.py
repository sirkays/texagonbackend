from konn3ct_client import Konn3ctAPI

TOKEN = "H9LBxlRkBWs2hb1mnZ0v2wzfhOqwfjCFaK73Jx99"

konn3ct = Konn3ctAPI(TOKEN)


# STEP 1 — CREATE ROOM
# room = konn3ct.create_room(
#     name="Board Strategy Meeting",
#     logout_url="https://yourapp.com/left",
#     welcome_message="Welcome Directors"
# )

# print(room)
# room_id = room["data"]["id"]
# print("Room Created:", room_id)


# STEP 2 — START ROOM
start = konn3ct.start_room(
    room_id=8824,
    name="Board Strategy Meeting",
    started_by="CEO",
    message="Session started",
    logout_url="https://yourapp.com/left"
)

print(start)

print("Room Started")


# STEP 3 — JOIN ROOM (returns join URL)
join = konn3ct.join_room(
    room_id=8824,
    name="John Director",
    email="john@company.com",
    role="moderator"
)
print(join)
print("Join Link:", join["data"])