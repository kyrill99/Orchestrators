from dotenv import load_dotenv
from google import genai

load_dotenv()  # loads GEMINI_API_KEY from .env into the environment

MODEL = "gemini-3.8-flash"

client = genai.Client()  # uses the GEMINI_API_KEY environment variable

# First turn
first = client.interactions.create(
    model=MODEL,
    input="Give me one sentence on what an orchestrator agent does.",
)
print(first.output_text)

# Follow-up turn: server-side state is carried via previous_interaction_id
second = client.interactions.create(
    model=MODEL,
    input="Now give a concrete example.",
    previous_interaction_id=first.id,
)
print(second.output_text)
