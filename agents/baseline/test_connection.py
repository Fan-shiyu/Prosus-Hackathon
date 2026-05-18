from __future__ import annotations

import json
import os
import sys

import litellm

from agents.runner import run_game
from dotenv import load_dotenv
# Load environment variables from the .env file
load_dotenv()

def test_llm_connection():
    # Set up the model and API key
    MODEL = os.getenv("AGENT_MODEL", "openai/gpt-4.1-mini")
    API_KEY = os.getenv("OPENAI_API_KEY")

    if not API_KEY:
        raise ValueError("OPENAI_API_KEY environment variable is not set")

    # Define a test question
    test_question = "What is the capital of France?"

    # Call the LLM with the test question
    try:
        response = litellm.completion(
            model=MODEL,
            messages=[
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": test_question},
            ],
            temperature=0.3,
            max_tokens=1000,
        )

        # Print the response
        print("Response from LLM:", response.choices[0].message.content)

        # Verify the response is not empty
        assert response.choices[0].message.content.strip() != "", "Response from LLM is empty"

        print("Test passed: LLM connection and response are working correctly.")

    except Exception as e:
        print(f"Test failed: {e}")
        raise

if __name__ == "__main__":
    test_llm_connection()