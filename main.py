from flask import Flask, request, session
from twilio.twiml.messaging_response import MessagingResponse
import os
import psycopg2
from dotenv import load_dotenv
from functools import partial
from psycopg2.extras import RealDictCursor
import random
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime, timedelta
from twilio.rest import Client

# Load environment variables
load_dotenv()
app = Flask(__name__)
app.secret_key = os.getenv('FLASK_SECRET_KEY')  # Secret key for session management

# Twilio Client
client = Client(os.getenv("TWILIO_ACCOUNT_SID"), os.getenv("TWILIO_AUTH_TOKEN"))

# Initialize scheduler for background tasks
scheduler = BackgroundScheduler()
scheduler.start()

# Database connection function
def get_db_connection():
    try:
        conn = psycopg2.connect(
            dbname=os.getenv("DB_NAME"),
            user=os.getenv("DB_USER"),
            password=os.getenv("DB_PASSWORD"),
            host="localhost"
        )
        return conn
    except psycopg2.Error as e:
        print(f"Error connecting to database: {e}")
        return None

def is_valid_phone_number(phone):
    # Basic validation for phone numbers
    return phone.startswith('+') and phone[1:].isdigit() and len(phone) >= 10

# Helper functions for database interactions
def get_user(phone_number):
    conn = get_db_connection()
    if conn is None:
        return None
    try:
        with conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT * FROM users WHERE phone_number = %s", (phone_number,))
                return cur.fetchone()
    except psycopg2.Error as e:
        print(f"Database error: {e}")
        return None

def register_user(phone_number, full_name, role, emergency_contact):
    conn = get_db_connection()
    if conn is None:
        print("Database connection failed.")
        return None
    try:
        with conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    INSERT INTO users (phone_number, full_name, role, emergency_contact)
                    VALUES (%s, %s, %s, %s)
                    RETURNING *;
                    """,
                    (phone_number, full_name, role, emergency_contact)
                )
                user = cur.fetchone()
                conn.commit()
                return user
    except psycopg2.Error as e:
        print(f"Database error during registration: {e}")
        return None

def create_ride(user_id, pickup_location, destination, driver_name, car_details, fare, duration):
    conn = get_db_connection()
    if conn is None:
        return None
    try:
        with conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    INSERT INTO rides (user_id, pickup_location, destination, driver_name, car_details, status, fare, duration)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING *;
                    """,
                    (user_id, pickup_location, destination, driver_name, car_details, 'requested', fare, duration)
                )
                ride = cur.fetchone()
                conn.commit()
                return ride
    except psycopg2.Error as e:
        print(f"Database error: {e}")
        return None

def update_ride_status(ride_id, status):
    conn = get_db_connection()
    if conn is None:
        return None
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE rides SET status = %s WHERE id = %s", (status, ride_id))
                conn.commit()
    except psycopg2.Error as e:
        print(f"Database error: {e}")

def get_ride_history(user_id):
    conn = get_db_connection()
    if conn is None:
        return []
    try:
        with conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT * FROM rides WHERE user_id = %s ORDER BY ride_time DESC", (user_id,))
                return cur.fetchall()
    except psycopg2.Error as e:
        print(f"Database error: {e}")
        return []

def add_feedback(ride_id, rating, comments):
    conn = get_db_connection()
    if conn is None:
        return None
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO feedback (ride_id, rating, comments)
                    VALUES (%s, %s, %s);
                    """,
                    (ride_id, rating, comments)
                )
                conn.commit()
    except psycopg2.Error as e:
        print(f"Database error: {e}")

def assign_driver():
    drivers = [
        {'name': 'Alice', 'car': 'Toyota Prius - XYZ 1234'},
        {'name': 'Bob', 'car': 'Honda Civic - ABC 5678'},
        {'name': 'Charlie', 'car': 'Ford Focus - DEF 9012'}
    ]
    driver = random.choice(drivers)
    fare = round(random.uniform(10, 50), 2)
    eta = random.randint(5, 15)
    return driver, fare, eta

# Ride Simulation Workflow
def schedule_ride_simulation(ride_id, phone_number, fare):
    def send_status_update(ride_id, status, message):
        update_ride_status(ride_id, status)
        try:
            client.messages.create(
                body=message,
                from_='whatsapp:' + os.getenv("TWILIO_WHATSAPP_NUMBER"),
                to=phone_number
            )
            print(f"Message sent: {message}")
        except Exception as e:
            print(f"Error sending message: {e}")

    # Schedule jobs with partial
    scheduler.add_job(partial(send_status_update, ride_id, "Driver is arriving", "Your driver is arriving!"),
                      'date', run_date=datetime.now() + timedelta(seconds=10))
    scheduler.add_job(partial(send_status_update, ride_id, "Trip started", "Your trip has started!"),
                      'date', run_date=datetime.now() + timedelta(seconds=20))
    scheduler.add_job(partial(send_status_update, ride_id, "Driving", "Driving..."),
                      'date', run_date=datetime.now() + timedelta(seconds=30))
    scheduler.add_job(partial(send_status_update, ride_id, "Completed",
                              f"You have arrived at your destination. Total fare: ${fare}. Thank you for riding with us!"),
                      'date', run_date=datetime.now() + timedelta(seconds=40))

# Handle Ride Request Workflow
def handle_ride_request(phone_number, incoming_msg):
    response = MessagingResponse()
    msg = response.message()

    if 'ride_step' not in session:
        session['ride_step'] = 'awaiting_pickup'
        msg.body("Please share your pickup location using WhatsApp's location sharing feature.")
        return str(response)

    if session['ride_step'] == 'awaiting_pickup':
        latitude = request.values.get('Latitude')
        longitude = request.values.get('Longitude')
        if latitude and longitude:
            session['pickup_location'] = f"{latitude},{longitude}"
            session['ride_step'] = 'awaiting_destination'
            msg.body("Thank you! Now, please share your destination location using WhatsApp's location sharing feature.")
        else:
            msg.body("Invalid location. Please share your pickup location using WhatsApp's location sharing feature.")
        return str(response)

    if session['ride_step'] == 'awaiting_destination':
        latitude = request.values.get('Latitude')
        longitude = request.values.get('Longitude')
        if latitude and longitude:
            session['destination_location'] = f"{latitude},{longitude}"
            driver, fare, eta = assign_driver()
            session['driver_info'] = driver
            session['fare'] = fare
            session['eta'] = eta

            # Create ride in the database
            user = get_user(phone_number)
            ride = create_ride(
                user_id=user['id'],
                pickup_location=session['pickup_location'],
                destination=session['destination_location'],
                driver_name=driver['name'],
                car_details=driver['car'],
                fare=fare,
                duration=eta
            )
            session['ride_id'] = ride['id']

            msg.body(f"Your driver {driver['name']} is on the way in a {driver['car']}. Estimated fare is ${fare}. Arrival time: {eta} minutes.")
            schedule_ride_simulation(ride['id'], 'whatsapp:+233541886845', fare)
            session.pop('ride_step', None)
        else:
            msg.body("Invalid location. Please share your destination location using WhatsApp's location sharing feature.")
        return str(response)

# Registration Workflow
def handle_registration(phone_number, incoming_msg):
    response = MessagingResponse()
    msg = response.message()

    # Debugging Log
    app.logger.info(f"Handling registration for {phone_number} - Step: {session.get('registration_step')}")

    # Initialize the registration flow
    if 'registration_step' not in session:
        session['registration_step'] = 'ask_name'
        msg.body("Hello! To register, please provide your full name.")
        return str(response)

    # Step: Ask for the name
    if session['registration_step'] == 'ask_name':
        session['full_name'] = incoming_msg
        session['registration_step'] = 'ask_role'
        msg.body("Thanks! Are you a driver or a passenger?")
        return str(response)

    # Step: Ask for the role
    elif session['registration_step'] == 'ask_role':
        if incoming_msg not in ['driver', 'passenger']:
            msg.body("Please respond with 'driver' or 'passenger'.")
            return str(response)
        session['role'] = incoming_msg.capitalize()
        session['registration_step'] = 'ask_emergency_contact'
        msg.body("Great! Please provide an emergency contact number.")
        return str(response)

    # Step: Ask for the emergency contact
    elif session['registration_step'] == 'ask_emergency_contact':
        if not is_valid_phone_number(incoming_msg):
            msg.body("Invalid phone number. Please provide a valid emergency contact number in the format +<country_code><number>.")
            return str(response)
        session['emergency_contact'] = incoming_msg
        full_name = session.pop('full_name')
        role = session.pop('role')
        emergency_contact = session.pop('emergency_contact')
        session.pop('registration_step', None)  # Clear the registration flow state

        # Register the user
        user = register_user(phone_number, full_name, role, emergency_contact)
        if user:
            msg.body(f"Thank you, {full_name}! You are now registered as a {role}.")
        else:
            msg.body("Sorry, there was an error with your registration. Please try again.")
        return str(response)



# Twilio webhook route
@app.route('/whatsapp', methods=['POST'])
def webhook():
    incoming_msg = request.values.get('Body', '').strip().lower()
    phone_number = request.values.get('From', '').replace('whatsapp:', '')

    # Logging incoming message and session state
    app.logger.info(f"Incoming message: {incoming_msg} from {phone_number}")
    app.logger.info(f"Session data: {session}")

    response = MessagingResponse()
    msg = response.message()

    user = get_user(phone_number)

    # Registration Process
    if not user:
        return handle_registration(phone_number, incoming_msg)

    # Check for ongoing ride request workflow
    if 'ride_step' in session:
        return handle_ride_request(phone_number, incoming_msg)

    # Handle commands for registered users
    if incoming_msg == 'request ride':
        return handle_ride_request(phone_number, incoming_msg)

    elif incoming_msg == 'view profile':
        profile_info = f"Name: {user['full_name']}\nRole: {user['role']}\nEmergency Contact: {user['emergency_contact']}"
        msg.body(profile_info)

    elif incoming_msg == 'ride history':
        rides = get_ride_history(user['id'])
        if rides:
            history = "\n".join([f"Ride on {ride['ride_time']} to {ride['destination']} - Fare: ${ride['fare']}, Status: {ride['status']}" for ride in rides])
            msg.body(history)
        else:
            msg.body("No ride history available.")

    elif incoming_msg.startswith("feedback"):
        parts = incoming_msg.split()
        if len(parts) >= 3:
            try:
                ride_id = int(parts[1])
                rating = int(parts[2])
                comments = " ".join(parts[3:]) if len(parts) > 3 else ""
                add_feedback(ride_id, rating, comments)
                msg.body("Thank you for your feedback!")
            except ValueError:
                msg.body("Invalid input. Please provide feedback in the format: feedback <ride_id> <rating> <comments>")
        else:
            msg.body("Please provide feedback in the format: feedback <ride_id> <rating> <comments>")

    else:
        msg.body("Hello There!" + "\n" + "Please use these commands to access the bot:" + "\n" + " 'request ride', 'view profile', 'ride history', or 'feedback <ride_id> <rating> <comments>'.")

    return str(response)

if __name__ == '__main__':
    debug_mode = os.getenv('FLASK_ENV', 'development') == 'development'
    app.run(debug=debug_mode, host='0.0.0.0', port=int(os.getenv('PORT', 5000)))

