#!/usr/bin/env python3
"""
Blogger Auto-Poster Pipeline
Fetches RSS, generates SEO articles with HF FLUX images, posts to Blogger.
"""

import sys
import os
import yaml
import json
import sqlite3
import hashlib
import logging
import re
import random
import time
import base64
from pathlib import Path
from datetime import datetime

import feedparser
import requests

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / 'posted_urls.db'
CREDENTIALS_PATH = BASE_DIR / 'client_secret.json'
TOKEN_PATH = BASE_DIR / 'token.json'

SCOPES = ['https://www.googleapis.com/auth/blogger']

EXTERNAL_SOURCES = [
    {"name": "TechCrunch", "url": "https://techcrunch.com", "search": "https://techcrunch.com/?s={}"},
    {"name": "The Verge", "url": "https://www.theverge.com", "search": "https://www.theverge.com/search?q={}"},
    {"name": "Wired", "url": "https://www.wired.com", "search": "https://www.wired.com/search/?q={}"},
    {"name": "Ars Technica", "url": "https://arstechnica.com", "search": "https://arstechnica.com/?s={}"},
    {"name": "CNET", "url": "https://www.cnet.com", "search": "https://www.cnet.com/search/?query={}"},
]


def load_config():
    with open(BASE_DIR / 'config.yaml', 'r') as f:
        return yaml.safe_load(f)


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS posted_urls (
            url_hash TEXT PRIMARY KEY,
            url TEXT,
            title TEXT,
            posted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    return conn


def is_posted(conn, url):
    url_hash = hashlib.md5(url.encode()).hexdigest()
    cursor = conn.execute('SELECT 1 FROM posted_urls WHERE url_hash = ?', (url_hash,))
    return cursor.fetchone() is not None


def mark_posted(conn, url, title):
    url_hash = hashlib.md5(url.encode()).hexdigest()
    conn.execute(
        'INSERT OR REPLACE INTO posted_urls (url_hash, url, title) VALUES (?, ?, ?)',
        (url_hash, url, title)
    )
    conn.commit()


def get_blogger_service():
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    creds = None
    if TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            from google.auth.transport.requests import Request
            creds.refresh(Request())
        else:
            raise ValueError("Invalid or missing Blogger credentials")
        
        with open(TOKEN_PATH, 'w') as token:
            token.write(creds.to_json())

    return build('blogger', 'v3', credentials=creds)


def get_blog_id(service, blog_url):
    resp = service.blogs().getByUrl(url=blog_url).execute()
    return resp['id']


def generate_image(prompt, config, max_retries=3):
    """Generate image using Hugging Face Inference API."""
    hf_token = os.environ.get('HF_TOKEN') or config.get('hf_token')
    if not hf_token:
        logger.warning("No HF token found")
        return None, None
    
    model = config.get('hf_model', 'black-forest-labs/FLUX.1-dev')
    api_url = f"https://api-inference.huggingface.co/models/{model}"
    headers = {"Authorization": f"Bearer {hf_token}"}
    
    for attempt in range(max_retries):
        try:
            response = requests.post(
                api_url,
                headers=headers,
                json={"inputs": prompt},
                timeout=120
            )
            
            if response.status_code == 200:
                content_type = response.headers.get('content-type', 'image/png')
                if 'image' in content_type:
                    return response.content, content_type
                else:
                    logger.warning(f"Unexpected content type: {content_type}")
                    return None, None
            elif response.status_code == 503:
                try:
                    wait_time = response.json().get('estimated_time', 30)
                except:
                    wait_time = 30
                logger.info(f"Model loading, waiting {wait_time}s...")
                time.sleep(wait_time)
                continue
            else:
                logger.error(f"HF API error: {response.status_code} - {response.text[:200]}")
                if attempt < max_retries - 1:
                    time.sleep(10)
                    continue
                return None, None
        except requests.Timeout:
            logger.error(f"Timeout (attempt {attempt+1})")
            if attempt < max_retries - 1:
                time.sleep(10)
                continue
            return None, None
        except Exception as e:
            logger.error(f"HF request error: {e}")
            if attempt < max_retries - 1:
                time.sleep(10)
                continue
            return None, None
    
    return None, None


def generate_image_pollinations(prompt, width=800, height=450):
    """Fallback: Pollinations.ai (free)."""
    url = f"https://image.pollinations.ai/prompt/{requests.utils.quote(prompt)}?width={width}&height={height}&nologo=true"
    try:
        response = requests.get(url, timeout=60, allow_redirects=True)
        if response.status_code == 200:
            return response.content, response.headers.get('content-type', 'image/jpeg')
    except Exception as e:
        logger.error(f"Pollinations error: {e}")
    return None, None


def generate_image_picsum(seed_phrase, width=800, height=450):
    """Final fallback: Picsum (random real photos)."""
    url = f"https://picsum.photos/seed/{seed_phrase}/{width}/{height}"
    try:
        response = requests.get(url, timeout=30, allow_redirects=True)
        if response.status_code == 200:
            return response.content, response.headers.get('content-type', 'image/jpeg')
    except Exception as e:
        logger.error(f"Picsum error: {e}")
    return None, None


def extract_keywords(title, content, count=5):
    from collections import Counter
    import html
    
    title = html.unescape(title)
    content = html.unescape(content)
    text = f"{title} {content}".lower()
    
    stop_words = {
        'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for',
        'of', 'with', 'by', 'is', 'it', 'this', 'that', 'are', 'was', 'were',
        'be', 'been', 'being', 'have', 'has', 'had', 'do', 'does', 'did',
        'will', 'would', 'could', 'should', 'may', 'might', 'can', 'shall',
        'not', 'no', 'nor', 'so', 'if', 'then', 'than', 'too', 'very',
        'gsmarena', 'engadget', 'techcrunch', 'verge', 'wired', 'cnet', 'zdnet',
        'mashable', 'business', 'standard', 'reuters', 'bloomberg', 'forbes',
        'nbsp', 'amp', 'new', 'one', 'two', 'first', 'last', 'over', 'after',
        'year', 'years', 'month', 'months', 'week', 'weeks', 'time', 'times',
        'people', 'company', 'companies', 'way', 'ways', 'thing', 'things',
    }
    
    words = re.findall(r'\b[a-z]{4,}\b', text)
    words = [w for w in words if w not in stop_words]
    counter = Counter(words)
    keywords = [word for word, _ in counter.most_common(count)]
    return keywords if keywords else ['tech', 'news']


def get_external_links(keywords, num_links=3):
    links = []
    used_sources = set()
    shuffled_keywords = keywords.copy()
    random.shuffle(shuffled_keywords)
    
    for keyword in shuffled_keywords:
        if len(links) >= num_links:
            break
        available = [s for s in EXTERNAL_SOURCES if s['name'] not in used_sources]
        if not available:
            break
        source = random.choice(available)
        used_sources.add(source['name'])
        
        links.append({
            'name': source['name'],
            'url': source['search'].format(keyword.replace(' ', '+')),
            'keyword': keyword,
            'anchor': f"Read more about {keyword} on {source['name']}"
        })
    
    return links


def generate_article(entry, config):
    title = entry.get('title', 'Untitled')
    summary = entry.get('summary', '')
    link = entry.get('link', '')
    published = entry.get('published', '')
    
    clean_summary = re.sub(r'<[^>]+>', '', summary).strip()
    article_title = f"{title} - What You Need to Know in 2026"
    keywords = extract_keywords(title, clean_summary, config['seo']['keywords_count'])
    
    # Generate image with fallback chain
    image_base64 = None
    image_html = ''
    
    # Build prompt
    keyword_str = ' '.join(keywords[:3])
    image_prompt = f"{keyword_str}, realistic photograph, professional"
    
    # Tier 1: Hugging Face
    logger.info(f"Trying Hugging Face for: {image_prompt}")
    image_data, content_type = generate_image(image_prompt, config)
    
    # Tier 2: Pollinations
    if not image_data:
        logger.info("HF failed, trying Pollinations")
        image_data, content_type = generate_image_pollinations(image_prompt)
    
    # Tier 3: Picsum
    if not image_data:
        logger.info("Using Picsum fallback")
        image_data, content_type = generate_image_picsum(keyword_str)
    
    if image_data:
        image_base64 = base64.b64encode(image_data).decode('utf-8')
        mime_type = content_type if content_type else 'image/jpeg'
        image_html = f'<img src="data:{mime_type};base64,{image_base64}" alt="{title}" style="max-width:100%;height:auto;margin:20px 0;" />'
        logger.info(f"Image generated: {len(image_data)} bytes")
    
    external_links = get_external_links(keywords, num_links=1)
    
    # Build article content
    article_content = f"""<div class="article-container">
        {image_html}
        <p class="article-intro"><strong>{clean_summary}</strong></p>
        
        <h2>Introduction</h2>
        <p>In today's rapidly evolving technology landscape, <em>{title.lower()}</em> has become a topic of significant 
        importance for industry professionals, enthusiasts, and everyday consumers alike.</p>
        
        <p>As we navigate through 2026, the technology sector continues to witness unprecedented changes. The news 
        surrounding {title.lower()} represents just one piece of a much larger puzzle.</p>
        
        <h2>Understanding the Core Development</h2>
        <p>At its heart, this development addresses fundamental challenges that have long plagued the technology 
        industry. By examining the underlying factors, we can gain a clearer picture.</p>
        
        <ul>
            <li><strong>Innovation and Advancement:</strong> How this pushes the boundaries of current technology</li>
            <li><strong>Market Impact:</strong> The potential effects on existing products and services</li>
            <li><strong>Consumer Experience:</strong> What end-users can expect</li>
            <li><strong>Industry Response:</strong> How competitors are reacting</li>
        </ul>
        
        <h3>Key Factors Driving This Change</h3>
        <p>Several converging factors have contributed. Advances in AI and ML create new opportunities. Shifting 
        consumer expectations push companies to deliver more sophisticated solutions.</p>
        
        <h2>Market Analysis and Industry Impact</h2>
        <p>The market implications extend far beyond the immediate news cycle. Investors and analysts are closely monitoring.</p>
        
        {('<p>More details at <a href="' + external_links[0]['url'] + '" target="_blank">' + external_links[0]['anchor'] + '</a> (' + external_links[0]['name'] + ')</p>') if external_links else ''}
        
        <h2>What This Means for Consumers</h2>
        <p>Enhanced capabilities, improved user experiences, and more competitive pricing are among potential outcomes.</p>
        
        <h2>Looking Ahead: Future Predictions</h2>
        <p>Continued innovation, increased competition, and evolving consumer expectations will drive further changes.</p>
        
        <h2>Conclusion</h2>
        <p>This development represents a significant moment in the ongoing evolution of the technology sector.</p>
        
        <hr />
        <p><em>Inspired by coverage from <a href="{link}" target="_blank">the original source</a>. Published on {published}.</em></p>
    </div>"""
    
    return {
        'title': article_title,
        'content': article_content,
        'labels': keywords[:5],
        'original_url': link,
        'external_links': external_links,
        'image_base64': image_base64
    }


def post_to_blogger(service, blog_id, article):
    body = {
        'kind': 'blogger#post',
        'blog': {'id': blog_id},
        'title': article['title'],
        'content': article['content'],
        'labels': article['labels']
    }
    resp = service.posts().insert(blogId=blog_id, body=body, isDraft=False).execute()
    return resp


def main():
    logger.info("=" * 60)
    logger.info("Starting Blogger Auto-Poster")
    
    config = load_config()
    conn = init_db()
    
    feed = feedparser.parse(config['feed_url'])
    if not feed.entries:
        logger.error("No entries found!")
        sys.exit(1)
    
    logger.info(f"Found {len(feed.entries)} entries")
    
    try:
        service = get_blogger_service()
        blog_id = get_blog_id(service, config['blog_url'])
        logger.info(f"Blog ID: {blog_id}")
    except Exception as e:
        logger.error(f"Blogger auth failed: {e}")
        sys.exit(1)
    
    posts_created = 0
    max_posts = config.get('max_posts_per_run', 3)
    
    for entry in feed.entries:
        if posts_created >= max_posts:
            break
        
        url = entry.get('link', '')
        title = entry.get('title', '')
        
        if not url or is_posted(conn, url):
            if title:
                logger.info(f"Skipping: {title[:50]}")
            continue
        
        logger.info(f"Processing: {title[:60]}")
        
        try:
            article = generate_article(entry, config)
            result = post_to_blogger(service, blog_id, article)
            logger.info(f"Posted: {result.get('url', 'N/A')}")
            posts_created += 1
            mark_posted(conn, url, title)
        except Exception as e:
            logger.error(f"Failed to post: {e}")
            continue
    
    logger.info(f"Complete. Posted: {posts_created}/{max_posts}")
    conn.close()
    return posts_created


if __name__ == '__main__':
    main()
