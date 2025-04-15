import os
import discord
from discord.ext import commands
from dotenv import load_dotenv
import psycopg2
import re
from selenium import webdriver
from selenium.webdriver.edge.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
import time
from bs4 import BeautifulSoup
import random

# Load environment variables
load_dotenv()

# Enable necessary intents
intents = discord.Intents.default()
intents.message_content = True

# Initialize the WebDriver globally
service = Service("C:\\Users\\Connor\\Downloads\\edgedriver_win64\\msedgedriver.exe")
driver = webdriver.Edge(service=service)

# Function to authenticate using the saved cookie
def authenticate():
    driver.get("https://www.linkedin.com/")
    time.sleep(2)

    if "feed" in driver.current_url:
        print("Already authenticated.")
        return

    # Fetch the cookie securely from .env
    li_at_cookie = os.getenv("LI_AT_COOKIE")

    if not li_at_cookie:
        print("❌ LinkedIn authentication cookie is missing. Please set LI_AT_COOKIE in .env.")
        return

    cookie = {
        'name': 'li_at',
        'value': li_at_cookie,
        'domain': 'linkedin.com'
    }
    driver.add_cookie(cookie)
    driver.refresh()
    time.sleep(2)
    print("✅ Successfully authenticated with cookie.")

authenticate()

# Database connection
def connect_db():
    return psycopg2.connect(
        dbname=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT")
    )

# Bot setup with intents
bot = commands.Bot(command_prefix="!", intents=intents)

# Function to match job locations with user preferences
def is_location_match(job_location, user_location):
    """Checks if the job location matches user preferences, allowing flexible formats."""
    if not job_location:
        return False
    
    job_location = job_location.lower()
    user_location = user_location.lower()

    # Exact match first
    if user_location in job_location:
        return True
    
    # Try a regex match for city/state variations
    pattern = rf"\b{re.escape(user_location.split(',')[0])}\b"
    return bool(re.search(pattern, job_location))

@bot.command()
async def ping(ctx):
    """Responds with 'Pong!' to test if the bot is running."""
    await ctx.send("Pong!")

@bot.command()
async def set_job_preferences(ctx, location: str, distance: int, *positions: str):
    user_id = ctx.author.id
    conn = connect_db()
    cur = conn.cursor()

    # Insert or update user preferences
    cur.execute("""
        INSERT INTO user_preferences (user_id, location, distance, positions)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (user_id) DO UPDATE 
        SET location = excluded.location,
            distance = excluded.distance,
            positions = excluded.positions;
    """, (user_id, location, distance, list(positions)))

    conn.commit()
    cur.close()
    conn.close()

    await ctx.send(f"✅ Preferences set! Location: {location}, Distance: {distance} miles, Positions: {', '.join(positions)}")

@bot.command()
async def get_job_preferences(ctx):
    user_id = ctx.author.id
    conn = connect_db()
    cur = conn.cursor()

    cur.execute("SELECT location, distance, positions FROM user_preferences WHERE user_id = %s;", (user_id,))
    result = cur.fetchone()

    cur.close()
    conn.close()

    if result:
        location, distance, positions = result
        await ctx.send(f"Your preferences: Location: {location}, Distance: {distance}, Positions: {positions}")
    else:
        await ctx.send("❌ No preferences found. Please set them using `!set_job_preferences`.")

# Function to check if a job matches user preferences
def is_match(job, positions, location):
    """Check if a job loosely matches the user's preferences."""
    job_title = job['job_title'].lower()
    job_location = job['location'].lower()

    title_match = any(pos.lower() in job_title for pos in positions.split(", "))
    location_match = any(loc.lower() in job_location for loc in location.split(", "))

    return title_match or location_match

@bot.command()
async def find_jobs(ctx, positions: str = None, location: str = None):
    """Fetches job listings from LinkedIn and formats them in an embed box.
       Uses saved preferences if no arguments are provided."""

    user_id = ctx.author.id
    conn = connect_db()
    cur = conn.cursor()

    # If no arguments are provided, fetch user's saved preferences
    if positions is None and location is None:
        cur.execute("SELECT positions, location FROM user_preferences WHERE user_id = %s;", (user_id,))
        result = cur.fetchone()
        if result:
            positions, location = result

            if isinstance(positions, list):
                positions = " ".join(positions)  

            await ctx.send(f"🔍 Searching based on your preferences: **{positions}** in **{location}**")
        else:
            await ctx.send("❌ No job preferences found. Use `!set_job_preferences` to save preferences first.")
            cur.close()
            conn.close()
            return
    cur.close()
    conn.close()

    # Build LinkedIn job search URL
    url = f"https://www.linkedin.com/jobs/search/?keywords={positions.replace(' ', '%20')}&location={location.replace(' ', '%20')}"

    # Navigate to LinkedIn job search page
    driver.get(url)
    time.sleep(3)

    # Parse the page with BeautifulSoup
    soup = BeautifulSoup(driver.page_source, 'html.parser')

    # Extract job listings
    job_listings = soup.find_all("div", class_="job-card-container")

    job_data = []
    for job_listing in job_listings:
        try:
            # Extract job title
            job_title_element = job_listing.find("a", class_="job-card-container__link")
            job_title = "Unknown Title"
            job_url = "#"
            if job_title_element:
                strong_element = job_title_element.find("strong")
                job_title = strong_element.get_text(strip=True) if strong_element else job_title_element.get_text(strip=True)
                job_url = "https://www.linkedin.com" + job_title_element['href']

            # Extract company name
            company_name_element = job_listing.find("div", class_="artdeco-entity-lockup__subtitle")
            company_name = company_name_element.get_text(strip=True) if company_name_element else "Unknown Company"

            # Extract job location
            location_element = job_listing.find("ul", class_="job-card-container__metadata-wrapper")
            job_location = location_element.find("span").get_text(strip=True) if location_element and location_element.find("span") else "Location not specified"

            print(f"Found job: {job_title} at {company_name} in {job_location}")

            job_data.append({
                "job_title": job_title,
                "company_name": company_name,
                "location": job_location,
                "job_url": job_url,
            })

        except Exception as e:
            print(f"Error extracting job details: {e}")

    # Pick 5 random jobs from all available listings
    random_jobs = random.sample(job_data, min(5, len(job_data)))

    # Send job listings to Discord using an embed message
    if random_jobs:
        for job in random_jobs:
            embed = discord.Embed(description=f"[**{job['job_title']}**]({job['job_url']})", color=discord.Color.blue())
            embed.add_field(name="🏢 Company", value=job['company_name'], inline=False)
            embed.add_field(name="📍 Location", value=job['location'], inline=False)
            embed.set_footer(text="Click the job title to apply!")

            await ctx.send(embed=embed)
    else:
        await ctx.send("❌ No jobs found.")




# Run the bot
bot.run(os.getenv("DISCORD_TOKEN"))