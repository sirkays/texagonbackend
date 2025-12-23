import random

def get_fun_activities():
    print("Answer a few questions and I'll suggest 10 fun things to do!\n")

    mood = input("How are you feeling today? (happy / bored / tired / adventurous): ").lower()
    place = input("Do you prefer indoors or outdoors? (indoors / outdoors): ").lower()
    company = input("Do you want to be alone or with others? (alone / others): ").lower()
    energy = input("What is your energy level? (low / medium / high): ").lower()

    activities = []

    if place == "indoors":
        activities.extend([
            "Watch a movie or series",
            "Try a new recipe",
            "Read a book or comic",
            "Play video games",
            "Learn a new skill online",
            "Organize your room",
            "Listen to a podcast",
            "Do a puzzle or board game",
            "Write a short story",
            "Try meditation or yoga"
        ])
    else:
        activities.extend([
            "Go for a walk",
            "Try cycling",
            "Have a picnic",
            "Explore a new place",
            "Go hiking",
            "Play an outdoor sport",
            "Photography walk",
            "Visit a park",
            "Watch the sunset",
            "Go jogging"
        ])

    if company == "others":
        activities.extend([
            "Play multiplayer games",
            "Have a group chat or video call",
            "Board games with friends",
            "Go out for food together",
            "Plan a small trip"
        ])
    else:
        activities.extend([
            "Journal your thoughts",
            "Practice mindfulness",
            "Solo movie night",
            "Learn something new",
            "Listen to music with headphones"
        ])

    if energy == "low":
        activities = [a for a in activities if a not in [
            "Go hiking", "Play an outdoor sport", "Go jogging"
        ]]
    elif energy == "high":
        activities.extend([
            "Try a workout challenge",
            "Dance to music",
            "Join a sports activity"
        ])

    # Remove duplicates and select 10 activities
    activities = list(set(activities))
    suggestions = random.sample(activities, min(10, len(activities)))

    print("\nHere are 10 fun things you can do:")
    for i, activity in enumerate(suggestions, 1):
        print(f"{i}. {activity}")

# Run the program
get_fun_activities()
