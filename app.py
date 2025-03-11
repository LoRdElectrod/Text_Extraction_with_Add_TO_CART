from flask import Flask, request, jsonify, render_template
import os
import requests
import mysql.connector
import re
import logging
from together import Together
from dotenv import load_dotenv
from fuzzywuzzy import process

# Load environment variables
load_dotenv()
TOGETHER_API_KEY = os.getenv("TOGETHER_API_KEY")
IMGUR_CLIENT_ID = os.getenv("IMGUR_CLIENT_ID")
DB_HOST = os.getenv("DB_HOST")
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_NAME = os.getenv("DB_NAME")

# Initialize Together AI client
client = Together(api_key=TOGETHER_API_KEY)

app = Flask(__name__, template_folder="templates")

# Enable logging
logging.basicConfig(level=logging.INFO)

# Global variable for cart
cart = []

# Function to upload image to Imgur
def upload_to_imgur(image_path):
    headers = {"Authorization": f"Client-ID {IMGUR_CLIENT_ID}"}
    with open(image_path, "rb") as image_file:
        response = requests.post("https://api.imgur.com/3/upload", headers=headers, files={"image": image_file})
    
    if response.status_code == 200:
        return response.json()["data"]["link"]
    else:
        raise Exception(f"Imgur upload failed: {response.json()}")

# Database connection
def get_db_connection():
    return mysql.connector.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME
    )

# Fetch all medicine names from the database
def fetch_all_medicines():
    connection = get_db_connection()
    cursor = connection.cursor(dictionary=True)
    cursor.execute("SELECT medicine FROM product_table_new")
    results = cursor.fetchall()
    cursor.close()
    connection.close()
    return [result['medicine'] for result in results]

# Search for medicine in the database
def search_medicine_in_db(medicine_name):
    connection = get_db_connection()
    cursor = connection.cursor(dictionary=True)
    cursor.execute("SELECT medicine FROM product_table_new WHERE medicine LIKE %s", (f"%{medicine_name}%",))
    results = cursor.fetchall()
    cursor.close()
    connection.close()
    return [result['medicine'] for result in results]

# Get similar medicine names using fuzzy matching
def get_similar_medicines(medicine_name, all_medicines, limit=5):
    matches = process.extract(medicine_name, all_medicines, limit=limit)
    return [match[0] for match in matches if match[1] > 50]

# Parse medicine name and quantity from text
def parse_medicine_and_quantity(text):
    match = re.match(r"([a-zA-Z\s]+)\s*(\d+)", text)
    if match:
        medicine_name = match.group(1).strip()
        quantity = match.group(2).strip()
        return medicine_name, quantity
    else:
        return text, "1"

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/process_image', methods=['POST'])
def process_image():
    try:
        if 'image' not in request.files:
            return jsonify({"error": "No image file provided"}), 400
        
        # Save uploaded image
        image_file = request.files['image']
        image_path = f"./temp/{image_file.filename}"
        os.makedirs("./temp", exist_ok=True)
        image_file.save(image_path)

        # Upload image to Imgur
        uploaded_image_url = upload_to_imgur(image_path)

        # Send request to Together AI's Vision Model
        response = client.chat.completions.create(
            model="meta-llama/Llama-3.2-11B-Vision-Instruct-Turbo",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Extract all medicine names and quantities from the image. Return the result as a list in the format: {Medicine Name} {Quantity}. Separate each item with a new line."},
                        {"type": "image_url", "image_url": {"url": uploaded_image_url}}
                    ]
                }
            ],
            max_tokens=None,
            temperature=0.7,
            top_p=0.7,
            top_k=50,
            repetition_penalty=1,
            stop=["<|eot_id|>", "<|eom_id|>"]
        )

        extracted_text = response.choices[0].message.content.strip()

        # Split extracted text into items
        items = extracted_text.split("\n")

        # Fetch all medicines from the database
        all_medicines = fetch_all_medicines()

        results = []
        for item in items:
            medicine_name, quantity = parse_medicine_and_quantity(item)

            # Search for medicine in the database
            matched_medicines = search_medicine_in_db(medicine_name)
            matched_medicine = matched_medicines[0] if matched_medicines else "No match found"

            # Get similar medicine names if no exact match is found
            suggestions = []
            if matched_medicine == "No match found":
                suggestions = get_similar_medicines(medicine_name, all_medicines)

            # Add to cart if medicine found
            if matched_medicine != "No match found":
                cart.append({"medicine": matched_medicine, "quantity": int(quantity)})

            results.append({
                "extracted_medicine": medicine_name,
                "matched_medicine": matched_medicine,
                "suggestions": suggestions,
                "quantity": quantity
            })

        return jsonify({"results": results, "cart": cart})

    except Exception as e:
        logging.error(f"Error processing image: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/get_cart', methods=['GET'])
def get_cart():
    return jsonify({"cart": cart})

@app.route('/remove_from_cart/<int:index>', methods=['DELETE'])
def remove_from_cart(index):
    try:
        if 0 <= index < len(cart):
            cart.pop(index)
        return jsonify({"cart": cart})
    except Exception as e:
        logging.error(f"Error removing item: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/update_cart/<int:index>/<string:change>', methods=['PUT'])
def update_cart(index, change):
    try:
        change = int(change)  # Convert manually
        print(f"Received update_cart request: index={index}, change={change}")

        if 0 <= index < len(cart):
            new_quantity = cart[index]['quantity'] + change
            if new_quantity >= 1:
                cart[index]['quantity'] = new_quantity
            else:
                cart.pop(index)  # Remove item if quantity goes to 0
            return jsonify({"cart": cart}), 200

        return jsonify({"error": "Invalid index"}), 400
    except ValueError:
        return jsonify({"error": "Invalid quantity value"}), 400


if __name__ == '__main__':
    app.run(debug=True)
