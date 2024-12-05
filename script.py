import requests
from bs4 import BeautifulSoup
import html2text
from ebooklib import epub
import subprocess
from pathlib import Path
import re
import time
import os
import logging
from urllib.parse import urlparse
from datetime import datetime

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def fetch_page_content(url, max_retries=3, retry_delay=5):
    """Fetch content from a webpage with retries"""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
    
    for attempt in range(max_retries):
        try:
            response = requests.get(url, headers=headers, timeout=30)
            response.raise_for_status()  # Raise an exception for bad status codes
            return response.text
        except requests.RequestException as e:
            logger.error(f"Attempt {attempt + 1}/{max_retries} failed for URL {url}: {str(e)}")
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
            else:
                raise

def extract_article_urls(main_page_content, base_url="https://jack-vanlightly.com"):
    """Extract article URLs from the main page"""
    soup = BeautifulSoup(main_page_content, 'html.parser')
    links = soup.find_all('a', href=True)
    article_urls = set()  # Use set to avoid duplicates
    
    for link in links:
        href = link['href']
        # Specifically look for analysis URLs
        if '/analyses/' in href or '/blog/' in href:
            full_url = href if href.startswith('http') else f"{base_url.rstrip('/')}/{href.lstrip('/')}"
            article_urls.add(full_url)
    
    return list(article_urls)

def sanitize_filename(title):
    """Convert title to a safe filename"""
    # Replace invalid filename characters
    safe_title = re.sub(r'[<>:"/\\|?*]', '_', title)
    # Limit length and remove trailing spaces/dots
    return safe_title[:100].strip('. ')

def create_epub(articles, output_dir="output", title="Downloaded Articles"):
    """Create an EPUB file from articles"""
    if not articles:
        logger.error("No articles to create EPUB from")
        return None
    
    try:
        # Create output directory if it doesn't exist
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        
        book = epub.EpubBook()
        book.set_identifier(f'articles-{int(time.time())}')
        book.set_title(title)
        book.set_language('en')
        
        chapters = []
        for i, (article_title, content) in enumerate(articles):
            chapter = epub.EpubHtml(title=article_title,
                                  file_name=f'chap_{i+1}.xhtml',
                                  content=f"<h1>{article_title}</h1>{content}")
            book.add_item(chapter)
            chapters.append(chapter)
        
        # Add navigation files
        book.toc = chapters
        book.add_item(epub.EpubNcx())
        book.add_item(epub.EpubNav())
        book.spine = ['nav'] + chapters
        
        # Generate filename from title
        epub_filename = f"{sanitize_filename(title)}.epub"
        epub_path = output_dir / epub_filename
        
        # Write EPUB file
        epub.write_epub(str(epub_path), book)
        logger.info(f"Created EPUB file: {epub_path}")
        return str(epub_path)
    
    except Exception as e:
        logger.error(f"Error creating EPUB: {str(e)}")
        return None

def convert_epub_to_mobi(epub_path):
    """Convert EPUB to MOBI using Calibre's ebook-convert"""
    try:
        mobi_path = str(Path(epub_path).with_suffix('.mobi'))
        subprocess.run(['ebook-convert', epub_path, mobi_path], 
                     check=True, 
                     capture_output=True, 
                     text=True)
        logger.info(f"Created MOBI file: {mobi_path}")
        return mobi_path
    except subprocess.CalledProcessError as e:
        logger.error(f"Error converting to MOBI: {e.stderr}")
        logger.error("Make sure Calibre is installed (brew install calibre)")
        return None
    except Exception as e:
        logger.error(f"Unexpected error during MOBI conversion: {str(e)}")
        return None

def extract_article_content(soup):
    """Extract article content with better error handling"""
    try:
        # Try different potential content containers
        content_div = None
        for class_name in ['post-content', 'post', 'article', 'entry-content']:
            content_div = soup.find('div', class_=class_name)
            if content_div:
                break
        
        if not content_div:
            logger.warning("Could not find content container")
            return None, None
        
        # Extract title - improved title extraction with better selectors
        title = None
        
        # Try to find title in specific blog structure
        header = soup.find('div', class_='post-header')
        if header:
            h1 = header.find('h1', class_='post-title')
            if h1:
                title = h1.text.strip()
        
        # Try meta title if header title not found
        if not title:
            meta_title = soup.find('meta', property='og:title')
            if meta_title:
                title = meta_title.get('content', '').strip()
        
        # Try document title if meta title not found
        if not title:
            doc_title = soup.find('title')
            if doc_title:
                title = doc_title.text.strip()
                # Remove blog name from title if present
                title = title.split('|')[0].strip()
        
        # Try standard heading tags if still no title
        if not title:
            for title_tag in ['h1', 'h2']:
                title_elem = soup.find(title_tag)
                if title_elem:
                    title = title_elem.text.strip()
                    break
        
        # Extract from URL as last resort
        if not title:
            try:
                url = soup.find('link', rel='canonical')['href']
                path_parts = urlparse(url).path.split('/')
                # Find the last meaningful part of the URL
                for part in reversed(path_parts):
                    if part and not part.isdigit():
                        title = part.replace('-', ' ').title()
                        break
            except:
                pass
        
        if not title:
            title = "Untitled Article"
            logger.warning("Could not find title, using default")
        
        # Clean up content
        # Remove unwanted elements
        for unwanted in content_div.find_all(['script', 'style', 'iframe']):
            unwanted.decompose()
        
        # Clean up the title
        title = title.replace('\n', ' ').strip()
        title = re.sub(r'\s+', ' ', title)  # Replace multiple spaces with single space
        
        content = str(content_div)
        return title, content
    
    except Exception as e:
        logger.error(f"Error extracting article content: {str(e)}")
        return None, None

def extract_article_date(url, soup):
    """Extract the publication date from the article"""
    try:
        # Try to extract date from URL first (most reliable for this blog)
        url_match = re.search(r'/(\d{4})/(\d{1,2})/(\d{1,2})/', url)
        if url_match:
            year, month, day = map(int, url_match.groups())
            return datetime(year, month, day)
        
        # Try meta tags
        meta_date = soup.find('meta', property=['article:published_time', 'og:published_time'])
        if meta_date:
            date_str = meta_date.get('content')
            return datetime.fromisoformat(date_str.split('T')[0])
        
        # Try looking for date in the post header
        date_elem = soup.find(class_=['post-date', 'date', 'published'])
        if date_elem:
            date_str = date_elem.text.strip()
            # Try different date formats
            for fmt in ['%B %d, %Y', '%Y-%m-%d', '%d/%m/%Y']:
                try:
                    return datetime.strptime(date_str, fmt)
                except ValueError:
                    continue
        
        # If no date found, use a very old date to put at the end
        return datetime(1900, 1, 1)
    except Exception as e:
        logger.warning(f"Could not extract date: {str(e)}")
        return datetime(1900, 1, 1)

def save_article_text(title, content, output_dir="output"):
    """Save article content as text file for backup"""
    try:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        
        # Create a unique filename using the title
        filename = f"{sanitize_filename(title)}.txt"
        file_path = output_dir / filename
        
        # If file exists, add a number to make it unique
        counter = 1
        while file_path.exists():
            filename = f"{sanitize_filename(title)}_{counter}.txt"
            file_path = output_dir / filename
            counter += 1
        
        converter = html2text.HTML2Text()
        converter.ignore_links = False
        text_content = converter.handle(content)
        
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(f"Title: {title}\n\n")
            f.write(text_content)
        
        logger.info(f"Saved text version: {file_path}")
        return str(file_path)
    
    except Exception as e:
        logger.error(f"Error saving text file: {str(e)}")
        return None

def main(main_url, output_dir="output"):
    logger.info(f"Starting article download from: {main_url}")
    
    try:
        # Create output directory
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        
        # Fetch and parse main page
        main_content = fetch_page_content(main_url)
        article_urls = extract_article_urls(main_content)
        
        logger.info(f"Found {len(article_urls)} articles")
        
        # List to store articles with their dates
        dated_articles = []
        
        # Process each article
        for i, url in enumerate(article_urls, 1):
            try:
                logger.info(f"Processing article {i}/{len(article_urls)}: {url}")
                
                # Fetch and parse article
                article_content = fetch_page_content(url)
                soup = BeautifulSoup(article_content, 'html.parser')
                
                # Extract article content and title
                title, content = extract_article_content(soup)
                if not title or not content:
                    logger.error(f"Failed to extract content from {url}")
                    continue
                
                # Extract article date
                pub_date = extract_article_date(url, soup)
                
                # Convert HTML to text for plain text version
                h = html2text.HTML2Text()
                h.ignore_links = False
                text_content = h.handle(content)
                
                # Save text version
                filename = f"{sanitize_filename(title)}.txt"
                text_path = output_dir / filename
                with open(text_path, 'w', encoding='utf-8') as f:
                    f.write(text_content)
                logger.info(f"Saved text version: {text_path}")
                
                # Store article with its date
                dated_articles.append((pub_date, title, content))
                logger.info(f"Successfully processed: {title} (Published: {pub_date.strftime('%Y-%m-%d')})")
                
            except Exception as e:
                logger.error(f"Error processing article {url}: {str(e)}")
                continue
        
        if not dated_articles:
            logger.error("No articles were successfully processed")
            return
        
        # Sort articles by date in reverse chronological order (newest first)
        dated_articles.sort(key=lambda x: x[0], reverse=True)
        logger.info("Sorted articles in reverse chronological order (newest first)")
        
        # Create EPUB with sorted articles
        logger.info("Creating EPUB file...")
        articles_for_epub = [(title, content) for _, title, content in dated_articles]
        epub_path = create_epub(articles_for_epub, output_dir=output_dir)
        
        if epub_path:
            # Convert to MOBI
            logger.info("Converting to MOBI format...")
            mobi_path = convert_epub_to_mobi(epub_path)
            if mobi_path:
                logger.info(f"Successfully created MOBI file: {mobi_path}")
            
        logger.info("Download process completed")
        
    except Exception as e:
        logger.error(f"Error in main process: {str(e)}")

if __name__ == "__main__":
    # You can customize these parameters
    MAIN_URL = "https://jack-vanlightly.com/analysis-archive"
    OUTPUT_DIR = "downloaded_articles"
    
    main(MAIN_URL, OUTPUT_DIR)