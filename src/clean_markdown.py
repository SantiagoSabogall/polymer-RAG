import re


class CleanError(Exception):
    pass


def extract_doi(text):
    patterns = [
        r'(?:https?://)?(?:dx\.)?doi\.org/(10\.\d{4,9}/[^\s,;\)<>\]]+)',
        r'DOI:\s*(10\.\d{4,9}/[^\s,;\)<>\]]+)',
        r'doi:\s*(10\.\d{4,9}/[^\s,;\)<>\]]+)',
        r'\b(10\.\d{4,9}/[^\s,;\)<>\]]+)\b',
    ]

    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            doi = match.group(1).rstrip('.')
            return doi
    return None


def remove_references(text):
    ref_headers = [
        r'(?i)^#+\s*references\s*$',
        r'(?i)^\s*references\s*$',
        r'(?i)^#+\s*bibliography\s*$',
        r'(?i)^\s*bibliography\s*$',
        r'(?i)^#+\s*literature\s+cited\s*$',
        r'(?i)^\s*literature\s+cited\s*$',
        r'(?i)^#+\s*acknowledgements?\s*$',
        r'(?i)^\s*acknowledgements?\s*$',
        r'(?i)^#+\s*acknowledgments?\s*$',
        r'(?i)^\s*acknowledgments?\s*$',
    ]

    end_sections = [
        r'(?i)^\s*Supplementary Materials:.*$',
        r'(?i)^\s*Additional supporting information.*$',
        r'(?i)^\s*Author Contributions:.*$',
        r'(?i)^\s*Funding:.*$',
        r'(?i)^\s*Institutional Review Board Statement:.*$',
        r'(?i)^\s*Informed Consent Statement:.*$',
        r'(?i)^#+\s*Data availability.*$',
        r'(?i)^\s*Data Availability Statement:.*$',
        r'(?i)^\s*DATA AVAILABILITY.*$',
        r'(?i)^#+\s*SUPPORTING INFORMATION\s*$',
        r'(?i)^\s*SUPPORTING INFORMATION\s*$',
        r'(?i)^\s*Conflicts? of Interest:.*$',
        r'(?i)^\s*Conﬂicts of Interest:.*$',
        r'(?i)^\s*Acknowledgements?:.*$',
    ]

    ref_line_patterns = [
        r'^\s*-\s*\d+\.\s+.*$',
        r'^\s*-\s*\[\d+\]\s+.*$',
        r'^\s*\[\d+\]\s+.*$',
        r'^\s*\d+\.\s+.*\.\s*\d{4}.*$',
        r'^\s*\d+\.\s+.*\[CrossRef\].*$',
        r'^\s*\d+\.\s+.*\[PubMed\].*$',
        r'^\s*\(\d{4}\)\.\s*$',
    ]

    orcid_patterns = [
        r'(?i)^.*ORCID.*$',
        r'(?i)^.*https?://example\.com.*$',
        r'^\d+,Downloadedfrom.*$',
        r'^\s*\(\d{4}\)\.\s*$',
    ]

    lines = text.split('\n')
    cleaned = []
    in_references = False

    for line in lines:
        if any(re.match(p, line.strip()) for p in ref_headers):
            in_references = True
            continue

        if in_references:
            if line.strip() == '':
                continue
            if any(re.match(p, line.strip()) for p in ref_line_patterns):
                continue
            if re.match(r'^\s*-\s*\d+\.', line.strip()):
                continue
            in_references = False

        if any(re.match(p, line.strip()) for p in ref_line_patterns):
            continue

        if any(re.match(p, line.strip()) for p in end_sections):
            continue

        if any(re.match(p, line.strip()) for p in orcid_patterns):
            continue

        cleaned.append(line)

    return '\n'.join(cleaned)


def remove_headers(text):
    header_patterns = [
        r'(?i)^.*Vol\.\s*\d+.*(?:p\.\s*\d+|pp\.\s*\d+).*$',
        r'(?i)^.*Volume\s*\d+.*(?:p\.\s*\d+|pp\.\s*\d+).*$',
        r'(?i)^.*Journal.*\d{4}.*(?:p\.\s*\d+|pp\.\s*\d+).*$',
        r'(?i)^See discussions, stats, and author profiles.*$',
        r'(?i)^Article in.*·\s*\w+\s*\d{4}$',
        r'(?i)^R E S E A R C H\s+A R T I C L E$',
        r'(?i)^ORIGINAL\s+RESEARCH$',
        r'(?i)^REVIEW\s+ARTICLE$',
    ]

    lines = text.split('\n')
    cleaned = []

    for line in lines:
        if any(re.match(p, line.strip()) for p in header_patterns):
            continue
        cleaned.append(line)

    return '\n'.join(cleaned)


def remove_footers(text):
    footer_patterns = [
        r'(?i)^\s*©.*$',
        r'(?i)^\s*Copyright.*$',
        r'(?i)^\s*DOI:\s*10\.\d+.*$',
        r'(?i)^\s*https?://doi\.org/.*$',
        r'(?i)^\s*Polym\s+Adv\s+Technol.*wileyonlinelibrary.*$',
        r'(?i)^\s*View publication stats\s*$',
        r'(?i)^\s*How to cite this article:.*$',
        r'(?i)^\s*.*wileyonlinelibrary\.com.*$',
        r'(?i)^\s*.*Downloaded from.*$',
        r'(?i)^\s*\d+,Downloadedfrom.*$',
    ]

    lines = text.split('\n')
    cleaned = []

    for line in lines:
        if any(re.match(p, line.strip()) for p in footer_patterns):
            continue
        cleaned.append(line)

    return '\n'.join(cleaned)


def remove_noise(text):
    noise_patterns = [
        r'(?i)^\s*CITATIONS?\s*$',
        r'(?i)^\s*\d+\s*$',
        r'(?i)^#+\s*\d+\s*author:?\s*$',
        r'(?i)^\s*\d+\s*author:?\s*$',
        r'(?i)^\s*SEE\s+PROFILE\s*$',
        r'(?i)^\s*READS\s*$',
        r'(?i)^\s*All content following this page was uploaded by.*$',
        r'(?i)^\s*The user has requested enhancement.*$',
        r'(?i)^\s*Funding information.*$',
        r'(?i)^#+\s*KEYWORDS\s*$',
        r'(?i)^\s*KEYWORDS\s*$',
        r'(?i)^#+\s*Keywords:?.*$',
        r'(?i)^\s*Keywords:?.*$',
        r'(?i)^\s*CONFLICT OF\s+INTEREST.*$',
        r'(?i)^\s*Received:.*Revised:.*Accepted:.*$',
        r'(?i)^\s*Correspondence\s+.*$',
        r'(?i)^\s*\* Corresponding author:.*$',
        r'(?i)^\s*\*Corresponding authors?:.*$',
        r'(?i)^\s*\d+Department.*$',
        r'(?i)^\s*\d+Centre.*$',
        r'(?i)^\s*\d+\s*PUBLICATIONS?\s+\d+\s*CITATIONS?.*$',
        r'(?i)^\s*\d+\s*PUBLICATIONS?.*$',
        r'(?i)^\s*\d+\s*CITATIONS?.*$',
        r'^_{5,}\s*$',
    ]

    lines = text.split('\n')
    cleaned = []

    for line in lines:
        if any(re.match(p, line.strip()) for p in noise_patterns):
            continue
        cleaned.append(line)

    return '\n'.join(cleaned)


def clean_markdown(markdown_text):
    try:
        text = remove_references(markdown_text)
        text = remove_headers(text)
        text = remove_footers(text)
        text = remove_noise(text)
        text = re.sub(r'\n{3,}', '\n\n', text)
        return text.strip()
    except Exception as e:
        raise CleanError(f"Error limpiando markdown: {e}")
