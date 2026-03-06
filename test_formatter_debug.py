
import sys
import os

# Set up paths
sys.path.append(os.path.join(os.getcwd(), 'src'))

from ambient.telegram.formatter import format_as_html

test_text = "This is a test <unclosed tag. **bold** works."
formatted = format_as_html(test_text)
print(f"Input: {test_text}")
print(f"Output: {formatted}")

test_text_2 = "JSON leak: {\"key\": \"value\"} and some < characters."
formatted_2 = format_as_html(test_text_2)
print(f"Input 2: {test_text_2}")
print(f"Output 2: {formatted_2}")
