#!/usr/bin/env python3
"""
Blogger Auto-Poster Pipeline
Fetches RSS, generates 2000+ word SEO articles, posts to Blogger.
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


def generate_meta_description(title, clean_summary, keywords):
    """Generate SEO meta description (150-160 chars)."""
    keyword_str = ', '.join(keywords[:3])
    desc = f"{title}: {clean_summary[:120]}... Latest news, analysis & updates on {keyword_str}."
    if len(desc) > 160:
        desc = desc[:157] + "..."
    return desc


def get_internal_link(service, blog_id, exclude_title=None):
    """Fetch a recent post for internal linking."""
    try:
        resp = service.posts().list(blogId=blog_id, maxResults=10, orderBy='PUBLISHED').execute()
        posts = resp.get('items', [])
        for post in posts:
            if exclude_title and post.get('title') == exclude_title:
                continue
            return {'title': post['title'], 'url': post['url']}
    except Exception:
        pass
    return None


def generate_faq_section(keywords, title):
    """Generate FAQ section based on article topic."""
    kw1 = keywords[0] if keywords else 'technology'
    kw2 = keywords[1] if len(keywords) > 1 else 'innovation'
    kw3 = keywords[2] if len(keywords) > 2 else 'market'
    
    faqs = [
        {
            'q': f"What does this mean for {kw1} in 2026?",
            'a': f"Industry analysts predict significant shifts in the {kw1} landscape, with increased competition and innovation driving new standards across the sector."
        },
        {
            'q': f"How will {kw2} be affected by these changes?",
            'a': f"The ripple effects on {kw2} are expected to be substantial, potentially reshaping how businesses and consumers interact with related technologies."
        },
        {
            'q': f"What should consumers know about {kw3}?",
            'a': f"Consumers should stay informed about {kw3} developments as they may impact pricing, availability, and feature sets in upcoming product releases."
        },
        {
            'q': f"Is this a long-term trend or short-term development?",
            'a': f"Based on current indicators, this appears to be part of a broader long-term trend rather than an isolated event, suggesting sustained evolution in the industry."
        },
    ]
    
    faq_html = '<div class="faq-section">\n<h2>Frequently Asked Questions</h2>\n'
    for faq in faqs:
        faq_html += f'<div class="faq-item">\n<h3>{faq["q"]}</h3>\n<p>{faq["a"]}</p>\n</div>\n'
    faq_html += '</div>\n'
    
    return faq_html


def generate_expert_quotes(keywords, title):
    """Generate realistic expert commentary."""
    kw1 = keywords[0] if keywords else 'technology'
    kw2 = keywords[1] if len(keywords) > 1 else 'innovation'
    
    quotes = [
        {
            'text': f"The developments we're seeing in {kw1} represent a fundamental shift in how the industry approaches {kw2}. Companies that adapt quickly will have a significant competitive advantage.",
            'author': 'Industry Analyst',
            'role': 'Technology Research'
        },
        {
            'text': f"This is exactly the kind of innovation the market has been waiting for. The intersection of {kw1} and {kw2} creates entirely new possibilities for both businesses and consumers.",
            'author': 'Market Strategist',
            'role': 'Tech Consulting'
        },
    ]
    
    quotes_html = '<div class="expert-quotes">\n<h2>Expert Perspectives</h2>\n'
    for quote in quotes:
        quotes_html += f'''<blockquote class="expert-quote">
<p>"{quote['text']}"</p>
<cite>— {quote['author']}, {quote['role']}</cite>
</blockquote>\n'''
    quotes_html += '</div>\n'
    
    return quotes_html


def generate_article(entry, config, service=None, blog_id=None):
    title = entry.get('title', 'Untitled')
    summary = entry.get('summary', '')
    link = entry.get('link', '')
    published = entry.get('published', '')
    
    clean_summary = re.sub(r'<[^>]+>', '', summary).strip()
    article_title = f"{title} - What You Need to Know in 2026"
    keywords = extract_keywords(title, clean_summary, config['seo']['keywords_count'])
    
    # Generate meta description for SEO
    meta_description = generate_meta_description(title, clean_summary, keywords)
    
    # Get external and internal links
    external_links = get_external_links(keywords, num_links=1)
    internal_link = None
    if service and blog_id:
        internal_link = get_internal_link(service, blog_id, exclude_title=article_title)
    
    internal_link_html = ''
    if internal_link:
        internal_link_html = f'<p><strong>You may also like:</strong> <a href="{internal_link["url"]}" title="{internal_link["title"]}">{internal_link["title"]}</a></p>'
    
    # Generate expert quotes and FAQ
    expert_quotes = generate_expert_quotes(keywords, title)
    faq_section = generate_faq_section(keywords, title)
    
    # Build comprehensive 2000+ word article
    kw0 = keywords[0] if keywords else 'technology'
    kw1 = keywords[1] if len(keywords) > 1 else 'innovation'
    kw2 = keywords[2] if len(keywords) > 2 else 'market'
    kw3 = keywords[3] if len(keywords) > 3 else 'development'
    kw4 = keywords[4] if len(keywords) > 4 else 'growth'
    kw_str = ', '.join(keywords[:3]) if len(keywords) >= 3 else 'innovation, market growth, and consumer adoption'
    
    article_content = f"""<div class="article-container">
    <p class="article-intro"><strong>{clean_summary}</strong></p>
    
    <h2>Introduction</h2>
    <p>In today's rapidly evolving technology landscape, <em>{title.lower()}</em> has emerged as a defining topic that captures the attention of industry professionals, tech enthusiasts, and everyday consumers alike. With {kw0} at the forefront of innovation, this development signals a major shift in how we interact with modern technology and what we can expect from the industry in the months ahead.</p>
    
    <p>As we navigate through 2026, the technology sector continues to witness unprecedented changes that reshape established norms and create new paradigms. The news surrounding {title.lower()} represents just one piece of a much larger puzzle that includes {kw_str}. Understanding these interconnected developments is crucial for anyone who wants to stay ahead of the curve in an increasingly competitive marketplace.</p>
    
    <p>The significance of this story extends far beyond the immediate headlines. It touches upon fundamental questions about where the industry is heading, how companies will compete for market share, and what consumers should expect from the products and services they rely on daily. In this comprehensive analysis, we break down every aspect of this development to give you the complete picture.</p>
    
    <h2>Background and Historical Context</h2>
    <p>To fully appreciate the importance of recent developments in {kw0}, it is essential to understand the historical context that has brought us to this point. The technology industry has undergone remarkable transformation over the past decade, with breakthrough innovations emerging at an accelerating pace. From the rise of artificial intelligence and machine learning to the proliferation of connected devices and cloud computing, each wave of innovation has built upon the last to create the dynamic landscape we see today.</p>
    
    <p>The journey toward this particular milestone has been marked by significant investments in research and development, strategic partnerships between major industry players, and a growing recognition that {kw1} will be a key differentiator in the years to come. Companies that recognized early the potential of these trends have positioned themselves as leaders, while others are now scrambling to catch up in an increasingly competitive environment.</p>
    
    <p>Industry veterans recall similar pivotal moments in the past — the introduction of the smartphone, the rise of social media, the shift to cloud computing — each of which fundamentally altered the technology landscape and created new winners and losers. Many analysts believe we are at another such inflection point, with the potential to reshape entire sectors and create new opportunities for innovation and growth.</p>
    
    <h2>Key Developments and Breaking Details</h2>
    <p>The latest developments in {kw0} reveal a complex picture of an industry in transition. At the core of this story is a convergence of technological advancement, shifting consumer expectations, and competitive pressure that is forcing companies to rethink their strategies and accelerate their innovation timelines.</p>
    
    <p>Several key factors stand out as particularly significant. First, the pace of technological advancement in {kw0} has exceeded many analysts' predictions, creating both opportunities and challenges for industry participants. Second, consumer expectations have evolved rapidly, with users demanding more sophisticated, seamless experiences that integrate multiple technologies into cohesive ecosystems. Third, competitive dynamics have intensified as new entrants and established players alike vie for dominance in high-growth segments of the market.</p>
    
    <p>The specific details surrounding {title.lower()} illustrate these broader trends in action. Whether we are examining the technical specifications of new products, the strategic decisions of major companies, or the market response to recent announcements, the underlying narrative is one of rapid evolution and increasing complexity. Each new development adds another layer to the story, revealing both the potential and the challenges that lie ahead.</p>
    
    <h3>Technical Specifications and Innovation</h3>
    <p>From a technical perspective, the innovations driving this development are nothing short of remarkable. Engineers and researchers have pushed the boundaries of what was previously thought possible, leveraging advances in materials science, software engineering, and manufacturing processes to deliver capabilities that would have seemed like science fiction just a few years ago.</p>
    
    <p>The technical achievements are particularly noteworthy when considered in the context of broader industry trends. Improvements in processing power, energy efficiency, and connectivity have enabled new applications and use cases that were previously impractical or impossible. At the same time, advances in artificial intelligence and machine learning have opened up new possibilities for automation, personalization, and predictive capabilities that are transforming how we interact with technology.</p>
    
    <h3>Strategic Implications for Industry Players</h3>
    <p>For technology companies, the strategic implications of these developments are profound. The competitive landscape is shifting rapidly, with new leaders emerging and established players facing pressure to adapt or risk losing market share. Companies that can successfully navigate these changes will be well-positioned to capitalize on new opportunities, while those that fail to keep pace may find themselves increasingly marginalized.</p>
    
    <p>The strategic calculus is further complicated by the need to balance short-term performance with long-term investment. Companies must continue to deliver strong results in their existing business while simultaneously investing in the technologies and capabilities that will drive future growth. This tension is particularly acute in the current environment, where the pace of change shows no signs of slowing down.</p>
    
    <h2>Market Analysis and Industry Impact</h2>
    <p>The market implications of recent developments in {kw0} extend far beyond the immediate news cycle, touching virtually every aspect of the technology ecosystem. Investors, analysts, and industry participants are all trying to gauge the potential impact on existing products, services, and business models, and the answers are far from straightforward.</p>
    
    <p>Early indicators suggest significant potential for growth and disruption across multiple sectors. Market analysts have been revising their forecasts upward in response to the latest developments, reflecting growing confidence that the industry is entering a new phase of expansion. However, there are also concerns about potential headwinds, including regulatory challenges, supply chain constraints, and macroeconomic uncertainties that could temper growth in the near term.</p>
    
    <p>The impact on specific market segments varies considerably. In some areas, the new developments are expected to accelerate existing trends, driving faster adoption and more rapid innovation. In others, they may disrupt established business models and create opportunities for new entrants to challenge incumbents. Understanding these dynamics is crucial for anyone with a stake in the technology industry.</p>
    
    <h3>Investment and Financial Considerations</h3>
    <p>From an investment perspective, the developments in {kw0} present both opportunities and risks. The potential for significant returns is attracting capital from a wide range of sources, including venture capital firms, private equity investors, and public market participants. At the same time, the inherent uncertainty of rapidly evolving markets means that investors must carefully evaluate the risk-return profile of any investment decision.</p>
    
    <p>Financial analysts note that the companies best positioned to benefit from these trends are those with strong balance sheets, proven execution capabilities, and a clear strategic vision for how to capitalize on emerging opportunities. Companies that lack these attributes may struggle to compete effectively, even if they operate in high-growth segments of the market.</p>
    
    {expert_quotes}
    
    <h2>Consumer Impact and Practical Implications</h2>
    <p>For consumers, the practical implications of these developments are both exciting and complex. On one hand, the pace of innovation promises to deliver new products and services that offer enhanced capabilities, improved user experiences, and greater value for money. On the other hand, the rapid pace of change can also create confusion and uncertainty, as consumers try to navigate an increasingly complex landscape of options and make informed purchasing decisions.</p>
    
    <p>The impact on consumer behavior is already becoming apparent. Early adopters are embracing new technologies and incorporating them into their daily lives, while more cautious consumers are taking a wait-and-see approach, preferring to let new products and services mature before making purchasing decisions. This divergence in consumer attitudes is creating a segmented market that companies must carefully navigate.</p>
    
    <h3>How This Affects Your Daily Tech Experience</h3>
    <p>In practical terms, the developments in {kw0} are likely to manifest in several ways that directly impact consumers. Product improvements may lead to better performance, longer battery life, and new features that enhance the user experience. Pricing dynamics could shift as competition intensifies and economies of scale kick in. And the availability of new services and applications may expand the ways in which consumers can use technology to solve problems and enhance their lives.</p>
    
    <p>Consumers should also be aware of potential trade-offs. New technologies sometimes come with compatibility requirements that may necessitate upgrades to existing devices or infrastructure. Privacy and security considerations may also evolve as new capabilities introduce new potential vulnerabilities. Staying informed about these issues is essential for making smart decisions in a rapidly changing environment.</p>
    
    <h2>Competitive Landscape and Key Players</h2>
    <p>The competitive landscape surrounding {kw0} is characterized by intense rivalry among established tech giants, innovative startups, and everything in between. Each player brings its own strengths and strategies to the table, creating a dynamic and unpredictable market environment that rewards innovation, execution, and strategic agility.</p>
    
    <p>Major technology companies have been investing heavily in {kw1} capabilities, recognizing that leadership in this area will be a key determinant of future success. These investments span research and development, acquisitions, partnerships, and talent acquisition, reflecting the strategic importance that industry leaders attach to maintaining their competitive positions.</p>
    
    <p>At the same time, smaller companies and startups are playing an increasingly important role in driving innovation. Freed from the constraints of legacy business models, these agile competitors are often able to move more quickly and take greater risks, pushing the boundaries of what is possible and challenging established players to keep up. The resulting competition benefits consumers through better products, lower prices, and more rapid innovation.</p>
    
    {('<p>More details at <a href="' + external_links[0]['url'] + '" target="_blank" rel="noopener">' + external_links[0]['anchor'] + '</a> (' + external_links[0]['name'] + ')</p>') if external_links else ''}
    
    <h2>Future Predictions and What to Watch</h2>
    <p>Looking ahead, the trajectory of {kw0} appears poised for continued evolution and growth. Industry experts predict that the coming months and years will bring a wave of new developments that build upon the foundation laid by recent announcements and innovations. Key trends to watch include the integration of artificial intelligence across product lines, the expansion of ecosystem-based business models, and increasing emphasis on sustainability and responsible innovation.</p>
    
    <p>Several specific developments are worth monitoring closely. First, the evolution of {kw1} technologies is expected to accelerate, driven by ongoing advances in hardware capabilities and software algorithms. Second, regulatory developments may shape the competitive landscape, particularly in areas related to data privacy, antitrust, and platform governance. Third, macroeconomic factors, including interest rates, inflation, and geopolitical dynamics, could influence the pace and direction of industry growth.</p>
    
    <p>Long-term predictions suggest that the industry is entering a transformative phase that will reshape not just individual companies but entire sectors of the economy. The companies and consumers who understand these trends and position themselves accordingly will be best positioned to thrive in the new landscape that is emerging.</p>
    
    {faq_section}
    
    <h2>Conclusion</h2>
    <p>The developments surrounding {title.lower()} represent a significant moment in the ongoing evolution of the technology sector. As the industry continues to navigate this period of rapid change and transformation, the ability to adapt, innovate, and execute will be the key differentiators that separate winners from losers in an increasingly competitive marketplace.</p>
    
    <p>For consumers, staying informed about these trends is more important than ever. The decisions made today — which products to buy, which services to subscribe to, which platforms to invest time and energy in — will have lasting implications for the technology experience of tomorrow. By understanding the forces shaping the industry and the potential outcomes of current developments, consumers can make smarter choices that align with their needs and values.</p>
    
    <p>As we look to the future, one thing is certain: the pace of innovation shows no signs of slowing down. The companies and individuals who embrace change, invest in learning, and maintain a forward-looking perspective will be best positioned to capitalize on the opportunities that lie ahead. Stay tuned for more updates on {kw_str} and the latest developments shaping the technology landscape.</p>
    
    {internal_link_html}
    
    <hr />
    <p><em>Inspired by coverage from <a href="{link}" target="_blank" rel="noopener">the original source</a>. Published on {published}.</em></p>
</div>"""
    
    return {
        'title': article_title,
        'content': article_content,
        'labels': keywords[:5],
        'original_url': link,
        'external_links': external_links,
        'meta_description': meta_description
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
            article = generate_article(entry, config, service=service, blog_id=blog_id)
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