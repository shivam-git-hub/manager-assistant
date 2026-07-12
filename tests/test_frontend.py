import os
import re

def test_frontend_vue_import():
    # Path to index.html
    html_path = "app/static/index.html"
    assert os.path.exists(html_path), "index.html file must exist"
    
    with open(html_path, "r", encoding="utf-8") as f:
        html_content = f.read()
        
    # Standard regex to extract content inside the <head> tags
    head_match = re.search(r"<head>(.*?)</head>", html_content, re.DOTALL | re.IGNORECASE)
    assert head_match is not None, "index.html must have a <head> block"
    
    head_content = head_match.group(1)
    
    # Assert that a script importing Vue 3 is present inside <head>
    # We support jsdelivr or unpkg scripts
    assert "vue" in head_content.lower(), "Vue 3 library CDN import is missing from the <head> block!"
    assert "lucide" in head_content.lower(), "Lucide library CDN import is missing from the <head> block!"
    assert "tailwindcss" in head_content.lower() or "cdn.tailwindcss.com" in head_content.lower(), "Tailwind CSS import is missing from the <head> block!"
