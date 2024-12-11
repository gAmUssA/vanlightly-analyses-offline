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
    """Extract article URLs from the main page maintaining their original order"""
    soup = BeautifulSoup(main_page_content, 'html.parser')
    links = soup.find_all('a', href=True)
    article_urls = []  # Use list to maintain order
    
    seen_urls = set()  # For deduplication while maintaining order
    for link in links:
        href = link['href']
        # Specifically look for analysis URLs
        if '/analyses/' in href or '/blog/' in href:
            full_url = href if href.startswith('http') else f"{base_url.rstrip('/')}/{href.lstrip('/')}"
            if full_url not in seen_urls:
                seen_urls.add(full_url)
                article_urls.append(full_url)
    
    return article_urls

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
        for i, (article_title, article_url, content) in enumerate(articles):
            chapter = epub.EpubHtml(title=article_title,
                                  file_name=f'chap_{i+1}.xhtml',
                                  content=f"<h1>{article_title}</h1><p>Source: <a href='{article_url}'>{article_url}</a></p>{content}")
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
    """Main function to download and convert articles"""
    try:
        # Create output directory
        os.makedirs(output_dir, exist_ok=True)
        
        # Fetch main page content
        main_page_content = fetch_page_content(main_url)
        if not main_page_content:
            logger.error("Failed to fetch main page content")
            return
        
        # Extract article URLs
        article_urls = extract_article_urls(main_page_content)
        if not article_urls:
            logger.error("No article URLs found")
            return
        
        # Sort URLs by date (newest first)
        sorted_urls = []
        for url in article_urls:
            try:
                content = fetch_page_content(url)
                if content:
                    soup = BeautifulSoup(content, 'html.parser')
                    date = extract_article_date(url, soup)
                    sorted_urls.append((date if date else datetime.min, url))
            except Exception as e:
                logger.error(f"Error processing URL {url}: {str(e)}")
                continue
        
        sorted_urls.sort(reverse=True)  # Sort by date, newest first
        article_urls = [url for _, url in sorted_urls]  # Extract just the URLs
        
        # Process each article
        articles = []
        for url in article_urls:
            try:
                content = fetch_page_content(url)
                if content:
                    soup = BeautifulSoup(content, 'html.parser')
                    title, article_content = extract_article_content(soup)
                    if title and article_content:
                        articles.append((title, url, article_content))
                        # Save article text for backup
                        save_article_text(title, article_content, output_dir)
            except Exception as e:
                logger.error(f"Error processing article {url}: {str(e)}")
                continue
        
        if not articles:
            logger.error("No articles were successfully processed")
            return
        
        # Create EPUB file
        epub_path = create_epub(articles, output_dir, "Jack Vanlightly Articles")
        if not epub_path:
            logger.error("Failed to create EPUB file")
            return
        
        # Convert to MOBI
        mobi_path = convert_epub_to_mobi(epub_path)
        if not mobi_path:
            logger.error("Failed to convert to MOBI")
            return
        
        logger.info("Successfully created EPUB and MOBI files")
        
    except Exception as e:
        logger.error(f"Error in main function: {str(e)}")

if __name__ == "__main__":
    # You can customize these parameters
    MAIN_URL = "https://jack-vanlightly.com/analysis-archive"
    OUTPUT_DIR = "downloaded_articles"
    
    try:
        main_content = fetch_page_content(MAIN_URL)
        article_urls = extract_article_urls(main_content)
        
        articles = []
        for url in article_urls:
            try:
                content = fetch_page_content(url)
                soup = BeautifulSoup(content, 'html.parser')
                title = soup.title.string if soup.title else "Untitled"
                article_content = extract_article_content(soup)
                articles.append((title, url, article_content))
            except Exception as e:
                logger.error(f"Error processing article {url}: {str(e)}")
        
        # Create EPUB with fixed filename
        epub_path = create_epub(articles, output_dir=OUTPUT_DIR, title="jack_vanlightly_articles")
        
        if epub_path:
            # Convert to MOBI with the same base filename
            convert_epub_to_mobi(epub_path)
            logger.info("Successfully created both EPUB and MOBI files")
        else:
            logger.error("Failed to create EPUB file")
            exit(1)
            
    except Exception as e:
        logger.error(f"Main process failed: {str(e)}")
        exit(1)