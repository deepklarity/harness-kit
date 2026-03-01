import requests
import time
import random

BASE_URL = "http://localhost:8000"

def check_health():
    try:
        resp = requests.get(f"{BASE_URL}/health")
        if resp.status_code == 200:
            print("✅ Service is healthy")
            return True
        else:
            print(f"❌ Service unhealthy: {resp.status_code}")
            return False
    except requests.exceptions.ConnectionError:
        print("❌ valid connection could not be made to the server. Is it running?")
        return False

def create_user(name, email):
    resp = requests.post(f"{BASE_URL}/users", json={"name": name, "email": email})
    if resp.status_code == 201:
        user = resp.json()
        print(f"✅ Created User: {user['name']} ({user['id']})")
        return user
    else:
        print(f"❌ Failed to create user {name}: {resp.text}")
        return None

def create_board(name, description):
    resp = requests.post(f"{BASE_URL}/boards", json={"name": name, "description": description})
    if resp.status_code == 201:
        board = resp.json()
        print(f"✅ Created Board: {board['name']} ({board['id']})")
        return board
    else:
        print(f"❌ Failed to create board {name}: {resp.text}")
        return None

def create_label(name, color):
    resp = requests.post(f"{BASE_URL}/labels", json={"name": name, "color": color})
    if resp.status_code == 201:
        label = resp.json()
        print(f"✅ Created Label: {label['name']} ({label['id']})")
        return label
    else:
        print(f"❌ Failed to create label {name}: {resp.text}")
        return None

def create_task(board_id, title, description, created_by, priority="MEDIUM"):
    resp = requests.post(f"{BASE_URL}/tasks", json={
        "board_id": board_id,
        "title": title,
        "description": description,
        "priority": priority,
        "created_by": created_by
    })
    if resp.status_code == 201:
        task = resp.json()
        print(f"✅ Created Task: {task['title']} ({task['id']})")
        return task
    else:
        print(f"❌ Failed to create task: {resp.text}")
        return None

def update_task_status(task_id, status, updated_by):
    resp = requests.put(f"{BASE_URL}/tasks/{task_id}", json={"status": status, "updated_by": updated_by})
    if resp.status_code == 200:
        print(f"🔄 Updated Task {task_id} status to {status} by {updated_by}")
    else:
        print(f"❌ Failed to update task status: {resp.text}")

def update_task_eta(task_id, dev_eta_hours, updated_by):
    # Convert hours to seconds for the API contract
    dev_eta_seconds = int(dev_eta_hours * 3600)
    resp = requests.put(f"{BASE_URL}/tasks/{task_id}", json={"dev_eta_seconds": dev_eta_seconds, "updated_by": updated_by})
    if resp.status_code == 200:
        print(f"⏰ Set Task {task_id} ETA to {dev_eta_hours}h ({dev_eta_seconds}s) by {updated_by}")
    else:
        print(f"❌ Failed to update task ETA: {resp.text}")

def assign_task(task_id, assignee_id, updated_by):
    resp = requests.post(f"{BASE_URL}/tasks/{task_id}/assign", json={"assignee_id": assignee_id, "updated_by": updated_by})
    if resp.status_code == 200:
        print(f"👤 Assigned Task {task_id} to User {assignee_id} by {updated_by}")
    else:
        print(f"❌ Failed to assign task: {resp.text}")

def add_labels(task_id, label_ids, updated_by):
    resp = requests.post(f"{BASE_URL}/tasks/{task_id}/labels", json={"label_ids": label_ids, "updated_by": updated_by})
    if resp.status_code == 200:
        print(f"🏷️ Added labels to Task {task_id} by {updated_by}")
    else:
        print(f"❌ Failed to add labels: {resp.text}")

def main():
    if not check_health():
        return

    # 1. Create Users
    users_data = [
        ("Alice Engineer", "alice@example.com"),
        ("Bob Manager", "bob@example.com"),
        ("Charlie Designer", "charlie@example.com"),
        ("Dave DevOps", "dave@example.com")
    ]
    
    # Try creating users
    for name, email in users_data:
        create_user(name, email)
    
    # Fetch all users (new and existing)
    resp = requests.get(f"{BASE_URL}/users")
    users = resp.json() if resp.status_code == 200 else []

    # 2. Create Labels
    labels_data = [
        ("Bug", "#ef4444"),         # Red
        ("Feature", "#3b82f6"),     # Blue
        ("Enhancement", "#22c55e"), # Green
        ("Documentation", "#eab308"), # Yellow
        ("Critical", "#7c3aed")     # Purple
    ]
    for name, color in labels_data:
        create_label(name, color)
    resp = requests.get(f"{BASE_URL}/labels")
    labels = resp.json() if resp.status_code == 200 else []
    
    # 3. Create Boards
    boards_data = [
        ("Engineering Sprint", "Main engineering board for current sprint"),
        ("Product Roadmap", "High level product roadmap"),
        ("Design System", "UI/UX tasks and components")
    ]
    for name, desc in boards_data:
        create_board(name, desc)
    resp = requests.get(f"{BASE_URL}/boards")
    boards = resp.json() if resp.status_code == 200 else []

    if not boards or not users:
        print("❌ Not enough data to create tasks")
        return

    eng_board = boards[0]
    prod_board = boards[1]
    design_board = boards[2]

    # 4. Create Tasks & Simulate Activity

    # Task 1: Initialize Repo (Engineering)
    t1 = create_task(eng_board['id'], "Repo Init", "Initialize repository and basic structure", users[0]['email'], "HIGH")
    if t1:
        assign_task(t1['id'], users[0]['id'], users[1]['email']) # Alice assigned by Bob
        time.sleep(0.5)
        update_task_status(t1['id'], "IN_PROGRESS", users[0]['email'])
        time.sleep(1)
        update_task_status(t1['id'], "DONE", users[0]['email'])

    # Task 2: Auth Flow (Engineering)
    t2 = create_task(eng_board['id'], "Auth Flow", "Implement Authentication Flow", users[1]['email'], "CRITICAL")
    if t2:
        add_labels(t2['id'], [labels[1]['id'], labels[4]['id']], users[1]['email']) # Feature, Critical
        assign_task(t2['id'], users[0]['id'], users[1]['email']) # Alice
        update_task_eta(t2['id'], 18.0, users[1]['email']) # 18 hours
        time.sleep(0.5)
        update_task_status(t2['id'], "IN_PROGRESS", users[0]['email'])
    
    # Task 3: API Design (Engineering)
    t3 = create_task(eng_board['id'], "API Design", "Design REST API Endpoints", users[1]['email'], "HIGH")
    if t3:
        assign_task(t3['id'], users[1]['id'], users[1]['email']) # Bob
        add_labels(t3['id'], [labels[3]['id']], users[1]['email']) # Documentation
        update_task_eta(t3['id'], 5.5, users[1]['email']) # 5.5 hours
        time.sleep(0.5)
        update_task_status(t3['id'], "REVIEW", users[1]['email'])

    # Task 4: User Research (Product)
    t4 = create_task(prod_board['id'], "User Research", "Conduct user interviews for new feature", users[1]['email'], "MEDIUM")
    if t4:
        assign_task(t4['id'], users[2]['id'], users[1]['email']) # Charlie
        add_labels(t4['id'], [labels[2]['id']], users[2]['email']) # Enhancement

    # Task 5: Component Library (Design)
    t5 = create_task(design_board['id'], "UI Components", "Create basic button and input components", users[2]['email'], "HIGH")
    if t5:
        assign_task(t5['id'], users[2]['id'], users[2]['email']) # Charlie
        time.sleep(0.5)
        update_task_status(t5['id'], "IN_PROGRESS", users[2]['email'])
        create_task(design_board['id'], "Color Palette", "Design Color Palette", users[2]['email'], "MEDIUM")

    # Task 6: Bug Fix (Engineering)
    t6 = create_task(eng_board['id'], "Logo Fix", "Fix login page alignment issue", users[3]['email'], "LOW")
    if t6:
        add_labels(t6['id'], [labels[0]['id']], users[3]['email']) # Bug
        assign_task(t6['id'], users[3]['id'], users[3]['email']) # Dave
        update_task_eta(t6['id'], 2.0, users[3]['email']) # 2 hours

    print("\n🎉 Dummy data population complete!")

if __name__ == "__main__":
    main()
