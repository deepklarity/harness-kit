#!/usr/bin/env python3
import sys
import os
import re
import html
import datetime

def escape_html(text):
    if not text:
        return ""
    return html.escape(html.unescape(str(text)))

def parse_markdown_tables(text):
    lines = text.split('\n')
    new_lines = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        # Look for markdown table format
        if stripped.startswith('|') and i + 1 < len(lines) and re.match(r'^\s*\|?[\s\-\|:]+\|?\s*$', lines[i+1]):
            headers = [cell.strip() for cell in line.split('|')[1:-1]]
            i += 2
            rows = []
            while i < len(lines) and lines[i].strip().startswith('|'):
                row_cells = [cell.strip() for cell in lines[i].split('|')[1:-1]]
                if len(row_cells) < len(headers):
                    row_cells += [''] * (len(headers) - len(row_cells))
                elif len(row_cells) > len(headers):
                    row_cells = row_cells[:len(headers)]
                # Process inline formatting on table cells
                processed_cells = []
                for cell in row_cells:
                    c = re.sub(r'`([^`\n]+)`', r'<code class="inline-code">\1</code>', cell)
                    c = re.sub(r'\*\*([^*]+)\*\*', r'<strong>\1</strong>', c)
                    processed_cells.append(c)
                rows.append(processed_cells)
                i += 1
            
            table_html = '<div class="table-container"><table><thead><tr>'
            for h in headers:
                table_html += f'<th>{escape_html(h)}</th>'
            table_html += '</tr></thead><tbody>'
            for r in rows:
                table_html += '<tr>'
                for cell in r:
                    table_html += f'<td>{cell}</td>'
                table_html += '</tr>'
            table_html += '</tbody></table></div>'
            new_lines.append(table_html)
        else:
            new_lines.append(line)
            i += 1
    return '\n'.join(new_lines)

def markdown_to_html(text):
    if not text:
        return ""
    # Unescape first to prevent double-escaping
    text = html.escape(html.unescape(str(text)))
    
    # Revert escape of tags we want to retain
    text = text.replace('&lt;code&gt;', '<code>').replace('&lt;/code&gt;', '</code>')
    text = text.replace('&lt;pre&gt;', '<pre>').replace('&lt;/pre&gt;', '</pre>')
    text = text.replace('&lt;a href=', '<a href=').replace('&lt;/a&gt;', '</a>')

    # Convert markdown tables
    text = parse_markdown_tables(text)

    # Process line-by-line block structure
    lines = text.split('\n')
    processed_lines = []
    in_ul = False
    in_ol = False
    
    for line in lines:
        stripped = line.strip()
        
        m_ul = re.match(r'^\s*-\s+(.+)$', line)
        if not m_ul:
            m_ul = re.match(r'^\s*\*\s+(.+)$', line)
        m_ol = re.match(r'^\s*\d+\.\s+(.+)$', line)
        m_h = re.match(r'^\s*(#{1,6})\s+(.+)$', line)
        
        if m_ul:
            if not in_ul:
                if in_ol:
                    processed_lines.append('</ol>')
                    in_ol = False
                processed_lines.append('<ul>')
                in_ul = True
            processed_lines.append(f'<li>{m_ul.group(1)}</li>')
        elif m_ol:
            if not in_ol:
                if in_ul:
                    processed_lines.append('</ul>')
                    in_ul = False
                processed_lines.append('<ol>')
                in_ol = True
            processed_lines.append(f'<li>{m_ol.group(1)}</li>')
        elif m_h or stripped.startswith('<') or stripped.startswith('```'):
            if in_ul:
                processed_lines.append('</ul>')
                in_ul = False
            if in_ol:
                processed_lines.append('</ol>')
                in_ol = False
            
            if m_h:
                level = len(m_h.group(1))
                content = m_h.group(2)
                processed_lines.append(f'<h{level}>{content}</h{level}>')
            else:
                processed_lines.append(line)
        else:
            if not stripped:
                processed_lines.append('')
            else:
                # Close lists if a regular paragraph starts (not indented)
                if not line.startswith(' ') and not line.startswith('\t'):
                    if in_ul:
                        processed_lines.append('</ul>')
                        in_ul = False
                    if in_ol:
                        processed_lines.append('</ol>')
                        in_ol = False
                processed_lines.append(line)
            
    if in_ul:
        processed_lines.append('</ul>')
    if in_ol:
        processed_lines.append('</ol>')
        
    final_blocks = []
    current_p = []
    
    for line in processed_lines:
        stripped = line.strip()
        is_block_elem = (
            stripped.startswith('<h') or 
            stripped.startswith('<ul') or 
            stripped.startswith('</ul>') or 
            stripped.startswith('<ol') or 
            stripped.startswith('</ol>') or 
            stripped.startswith('<li') or 
            stripped.startswith('<pre') or 
            stripped.startswith('</pre>') or 
            stripped.startswith('<div') or 
            stripped.startswith('</div>') or 
            stripped.startswith('<table') or 
            stripped.startswith('</table>') or
            stripped.startswith('<tr') or
            stripped.startswith('</tr>')
        )
        
        if is_block_elem or not stripped:
            if current_p:
                final_blocks.append(f'<p>{" ".join(current_p)}</p>')
                current_p = []
            if stripped:
                final_blocks.append(line)
        else:
            current_p.append(line)
            
    if current_p:
        final_blocks.append(f'<p>{" ".join(current_p)}</p>')
        
    text = '\n'.join(final_blocks)

    # Process inline formatting
    text = re.sub(r'`([^`\n]+)`', r'<code class="inline-code">\1</code>', text)
    text = re.sub(r'\*\*([^*]+)\*\*', r'<strong>\1</strong>', text)

    return text

def parse_markdown(filepath):
    if not os.path.exists(filepath):
        print(f"Error: File not found at {filepath}", file=sys.stderr)
        sys.exit(1)

    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    content = content.replace('\r\n', '\n')

    # Extract Title
    title_match = re.search(r'^#\s+(.+)$', content, re.MULTILINE)
    title = title_match.group(1).strip() if title_match else "AI Slop Audit Report"

    # Extract Meta
    metadata = {}
    for key in ["Repository", "Audited integration branch", "Scope", "Method", "Date"]:
        m = re.search(rf'^\*\*{key}:\*\*\s*(.+)$', content, re.MULTILINE | re.IGNORECASE)
        if m:
            metadata[key] = m.group(1).strip()

    # Split into sections by '##' headers
    parts = re.split(r'\n##\s+', '\n' + content)
    
    intro_part = parts[0]
    disclaimer_match = re.search(r'\n>\s*(.+?)(?=\n---|^-|\n\n|$)', intro_part, re.DOTALL | re.MULTILINE)
    disclaimer = disclaimer_match.group(1).strip() if disclaimer_match else ""

    sections = {}
    for part in parts[1:]:
        lines = part.strip().split('\n')
        if not lines:
            continue
        header = lines[0].strip()
        body = '\n'.join(lines[1:]).strip()
        clean_header = re.sub(r'^\d+\.\s+', '', header)
        sections[clean_header] = body

    return title, metadata, disclaimer, sections

def clean_finding_title(title):
    if not title:
        return ""
    t = title.replace('`', '').strip()
    t = re.sub(r'^#+\s*', '', t)
    prev = None
    while prev != t:
        prev = t
        t = re.sub(r'^\[?(?:Finding|F|P|B|S|[A-Za-z])\s*\d+\]?\s*[\.\s\-—:|]+\s*', '', t, flags=re.IGNORECASE).strip()
        t = re.sub(r'^(?:CRITICAL|HIGH|MEDIUM|LOW|P0|P1|P2|P3|P4|BLOCKER|WARNING|INFO|SEVERITY\s*[:\-]\s*(?:CRITICAL|HIGH|MEDIUM|LOW))\s*[\.\s\-—:|]+\s*', '', t, flags=re.IGNORECASE).strip()
        t = re.sub(r'^\d+[\.\s\-—:]+\s*', '', t, flags=re.IGNORECASE).strip()
        t = re.sub(r'^\d+\.\d+[\.\s\-—:]+\s*', '', t, flags=re.IGNORECASE).strip()
        t = re.sub(r'^[:\-—|\s]+', '', t).strip()
        t = re.sub(r'\s*[:\-—|]+$', '', t).strip()
    return t

def clean_finding_location(raw_loc):
    """
    Strips narrative comments and extraneous text from location strings.
    Returns (clean_location, optional_comment).
    """
    if not raw_loc:
        return "", ""
    loc_clean = raw_loc.replace('`', '').strip()
    loc_clean = re.sub(r'^(?:\*\*|\b)Location(?:\*\*|)\s*[:\-]\s*', '', loc_clean, flags=re.IGNORECASE).strip()
    
    file_pattern = r'([\w\-\.\/\\:]+\.(?:tsx?|jsx?|py|json|dart|go|c|cpp|h|html|css|yaml|yml|rs|java)(?::\d+(?:-\d+)?)?)'
    m_file = re.search(file_pattern, loc_clean, re.IGNORECASE)
    if m_file:
        file_loc = m_file.group(1).strip()
        remainder = loc_clean[:m_file.start()] + " " + loc_clean[m_file.end():]
        remainder = re.sub(r'^[()/\-—:,\s]+', '', remainder)
        remainder = re.sub(r'[()/\-—:,\s]+$', '', remainder).strip()
        return file_loc, remainder

    # Fallback pattern for generic paths without recognized extension
    m_path = re.match(r'^([\w\-\.\/\\:]+(?::\d+(?:-\d+)?)?)(?:\s*(?:[.,;—\-\(\)\/]\s*|\s{2,}|\n)(.*))?$', loc_clean)
    if m_path:
        loc_part = m_path.group(1).strip().rstrip('.,;:')
        comment_part = m_path.group(2).strip().strip('()/-— ') if m_path.group(2) else ""
        return loc_part, comment_part
    
    return loc_clean, ""

def detect_code_language(file_path):
    if not file_path:
        return "typescript"
    ext = os.path.splitext(file_path.split(':')[0].strip())[1].lower().lstrip('.')
    mapping = {
        'ts': 'typescript',
        'tsx': 'tsx',
        'js': 'javascript',
        'jsx': 'jsx',
        'py': 'python',
        'json': 'json',
        'html': 'html',
        'css': 'css',
        'go': 'go',
        'rs': 'rust',
        'java': 'java',
        'c': 'c',
        'cpp': 'cpp',
        'h': 'c',
        'hpp': 'cpp',
        'dart': 'dart',
        'sh': 'bash',
        'yaml': 'yaml',
        'yml': 'yaml',
        'sql': 'sql',
    }
    return mapping.get(ext, ext if ext else "typescript")

def extract_code_from_file_if_needed(location, existing_snippet, proj_dir):
    has_real_code = False
    if existing_snippet and existing_snippet.strip():
        lines = existing_snippet.strip().splitlines()
        non_comment_lines = [l for l in lines if not l.strip().startswith('//') and not l.strip().startswith('#') and l.strip()]
        if len(non_comment_lines) > 0 or len(lines) > 2:
            has_real_code = True
            
    if has_real_code:
        return existing_snippet

    if not location or not proj_dir:
        return existing_snippet

    m_loc = re.search(r'([\w\-\./\\]+\.[a-zA-Z0-9]+)(?::(\d+)(?:-(\d+))?)?', location)
    if not m_loc:
        return existing_snippet

    rel_file = m_loc.group(1)
    start_line = int(m_loc.group(2)) if m_loc.group(2) else None
    end_line = int(m_loc.group(3)) if m_loc.group(3) else (start_line if start_line else None)

    possible_paths = [
        os.path.join(proj_dir, rel_file),
        os.path.abspath(rel_file)
    ]
    target_path = None
    for p in possible_paths:
        if os.path.isfile(p):
            target_path = p
            break
            
    if not target_path and os.path.isdir(proj_dir):
        base = os.path.basename(rel_file)
        for root, _, files in os.walk(proj_dir):
            if base in files:
                target_path = os.path.join(root, base)
                break

    if target_path and os.path.isfile(target_path):
        try:
            with open(target_path, 'r', encoding='utf-8', errors='ignore') as f:
                file_lines = f.readlines()
            if start_line and start_line <= len(file_lines):
                s_idx = max(0, start_line - 1)
                e_idx = min(len(file_lines), end_line if end_line else (s_idx + 10))
                if e_idx - s_idx < 3:
                    s_idx = max(0, s_idx - 1)
                    e_idx = min(len(file_lines), e_idx + 2)
                snippet_lines = file_lines[s_idx:e_idx]
                if snippet_lines:
                    return "".join(snippet_lines).rstrip()
            elif len(file_lines) > 0:
                return "".join(file_lines[:15]).rstrip()
        except Exception:
            pass

    return existing_snippet

def parse_findings(findings_body):
    findings = []
    parts = re.split(r'\n###\s+', '\n' + findings_body)
    
    start_index = 0
    if parts and not re.search(r'(?:\[?[FP]?\d+\]?|\d+\.)', parts[0].strip()):
        start_index = 1

    for part in parts[start_index:]:
        if not part.strip():
            continue
        lines = part.strip().split('\n')
        header = lines[0].strip()
        body = '\n'.join(lines[1:]).strip()
        
        header_clean = header.replace('`', '').strip()
        
        m_id = re.match(r'^(?:\[?(F|P)?(\d+)\]?\.?:?\s*)?(?:(CRITICAL|HIGH|MEDIUM|LOW|P0|P1|P2|P3|P4)\s*[-—:]\s*)?(.*)$', header_clean, re.IGNORECASE)
        raw_id_num = m_id.group(2) if m_id else None
        raw_sev_word = m_id.group(3) if m_id else None
        rest_title = m_id.group(4).strip() if (m_id and m_id.group(4)) else header_clean

        fid = f"F{raw_id_num}" if raw_id_num else f"F{len(findings)+1}"

        # Clean title thoroughly from any severity prefixes and IDs
        title = clean_finding_title(rest_title)

        # Parse severity
        likelihood = "Unknown"
        if raw_sev_word:
            likelihood = map_severity(raw_sev_word)
        elif any(w in header_clean.upper() for w in ["CRITICAL", "HIGH", "P0", "P1"]):
            likelihood = "High"
        elif any(w in header_clean.upper() for w in ["MEDIUM", "P2", "P3"]):
            likelihood = "Medium"
        elif any(w in header_clean.upper() for w in ["LOW", "P4"]):
            likelihood = "Low"

        # Parse Location from first few lines or explicit Location line
        raw_location = ""
        remaining_body = body
        body_lines_all = body.split('\n')
        for idx_l, bl in enumerate(body_lines_all[:6]):
            bl_str = bl.strip()
            if not bl_str:
                continue
            if re.match(r'^(?:\*\*|\b)Location(?:\*\*|)\s*[:\-]\s*', bl_str, re.IGNORECASE):
                raw_location = re.sub(r'^(?:\*\*|\b)Location(?:\*\*|)\s*[:\-]\s*', '', bl_str, flags=re.IGNORECASE).replace('`', '').strip()
                break
            elif (bl_str.startswith("`") and bl_str.endswith("`")) or (len(bl_str) < 500 and ("/" in bl_str or "\\" in bl_str or ":" in bl_str or ".ts" in bl_str or ".js" in bl_str or ".py" in bl_str or ".json" in bl_str) and not bl_str.startswith("-") and not bl_str.startswith("*") and not bl_str.startswith("#")):
                if not raw_location:
                    raw_location = bl_str.replace("`", "").strip()
        
        location, loc_comment = clean_finding_location(raw_location)

        # Check in the body if likelihood is still Unknown
        if likelihood == "Unknown":
            body_lines = [l.strip() for l in body_lines_all[:8] if l.strip()]
            for bl in body_lines:
                m_lik = re.search(r'(?:\*\*|\b)(?:Likelihood|Severity|Slop State|Slop Likelihood|Priority)\b(?:\*\*|)\s*[:\-]\s*\*?\*?([A-Za-z0-9\-\s%/()]+)', bl, re.IGNORECASE)
                if m_lik:
                    val = m_lik.group(1).strip().replace('*', '').strip()
                    likelihood = map_severity(val)
                    break

        # Parse Risk/Impact
        risk = "Unknown"
        body_lines = [l.strip() for l in body_lines_all[:8] if l.strip()]
        for bl in body_lines:
            m_risk = re.search(r'(?:\*\*|\b)(?:Risk|Impact)\b(?:\*\*|)\s*[:\-]\s*\*?\*?([A-Za-z0-9\-\s%/()]+)', bl, re.IGNORECASE)
            if m_risk:
                val = m_risk.group(1).strip().replace('*', '').strip()
                risk = map_impact(val)
                break
                
        # Search for explicit block if not found
        if risk == "Unknown":
            risk_match = re.search(r'\*\*(?:Risk|Impact|Why it matters):\*\*\s*([^\n]+)', remaining_body, re.IGNORECASE)
            if risk_match:
                risk = map_impact(risk_match.group(1).strip())

        # Synchronize if one was specified and the other wasn't
        if likelihood == "Unknown" and risk != "Unknown":
            likelihood = map_severity(risk)
        elif risk == "Unknown" and likelihood != "Unknown":
            risk = map_impact(likelihood)
        elif likelihood == "Unknown" and risk == "Unknown":
            likelihood = "Medium"
            risk = "Medium"

        # Extract code block
        code_match = re.search(r'^[ \t]*```(\w+)?\n([\s\S]+?)\n[ \t]*```', remaining_body, re.MULTILINE)
        code_lang = code_match.group(1) if code_match and code_match.group(1) else ""
        code_snippet = ""
        if code_match:
            raw_code = code_match.group(2)
            opening_pos = code_match.start()
            prev_newline = remaining_body.rfind('\n', 0, opening_pos)
            line_before = remaining_body[prev_newline + 1:opening_pos]
            indent_match = re.match(r'^[ \t]*', line_before)
            indent = indent_match.group(0) if indent_match else ""
            
            if indent:
                cleaned_lines = []
                for line in raw_code.split('\n'):
                    if line.startswith(indent):
                        cleaned_lines.append(line[len(indent):])
                    else:
                        cleaned_lines.append(line)
                code_snippet = '\n'.join(cleaned_lines)
            else:
                code_snippet = raw_code
        
        # Extract Reasoning (Why this signals slop)
        reasoning_match = re.search(
            r'\*\*(?:Why this (?:signals|is a|represents|is)(?: \(\w+\))?\s*(?:AI\s+|AI-generated\s+|high\s+likelihood\s+of\s+)?slop(?: signal)?|Reasoning|Why high likelihood of slop):\*\*\s*([\s\S]+?)(?=\n\*\*|\n###|$)',
            remaining_body,
            re.IGNORECASE
        )
        reasoning = reasoning_match.group(1).strip() if reasoning_match else ""
        if not reasoning:
            # Try a looser pattern that does NOT match Risk or Impact
            reasoning_match_loose = re.search(
                r'\*\*(?:Why this is slop|Audit Finding Details):\*\*\s*([\s\S]+?)(?=\n\*\*|\n###|$)',
                remaining_body,
                re.IGNORECASE
            )
            reasoning = reasoning_match_loose.group(1).strip() if reasoning_match_loose else "Forensic inspection of codebase pattern."
        reasoning = re.sub(r'\n*---\s*$', '', reasoning).strip()
        
        # Cleanup description
        description = remaining_body
        desc_lines = description.split('\n')
        cleaned_desc_lines = []
        for line in desc_lines:
            is_meta = False
            # If the line contains Likelihood: or Severity: or Risk: or Impact: as key metadata (e.g. Likelihood: HIGH | Risk: Critical)
            if re.search(r'(?:\*\*|\b)(?:Likelihood|Severity)\b', line, re.IGNORECASE) or (re.search(r'(?:\*\*|\b)(?:Risk|Impact)\b', line, re.IGNORECASE) and '|' in line):
                is_meta = True
            if is_meta:
                continue
            cleaned_desc_lines.append(line)
        description = '\n'.join(cleaned_desc_lines)

        description = re.sub(r'^\*\*Location:\*\*.*$', '', description, flags=re.MULTILINE | re.IGNORECASE)
        description = re.sub(r'^[ \t]*```(\w+)?\n([\s\S]+?)\n[ \t]*```', '', description, flags=re.MULTILINE)
        
        description = re.sub(
            r'\*\*(?:Why this (?:signals|is a|represents)(?: \(\w+\))?\s*(?:AI\s+|AI-generated\s+)?slop(?: signal)?|Why it matters|Reasoning):\*\*[\s\S]+?(?=\n\*\*|\n###|$)',
            '',
            description,
            flags=re.IGNORECASE
        )
        description = re.sub(
            r'\*\*(?:Why this is a(?: \(\w+\))? slop signal|Why it matters|Risk|Impact|Reasoning):\*\*[\s\S]+?(?=\n\*\*|\n###|$)',
            '',
            description,
            flags=re.IGNORECASE
        )
        description = re.sub(
            r'\*\*(?:Risk|Impact|Why it matters):\*\*[\s\S]+?(?=\n\*\*|\n###|$)',
            '',
            description,
            flags=re.IGNORECASE
        )
        description = re.sub(r'\n*---\s*$', '', description).strip()
        
        # Smart extraction of reasoning from description paragraphs if no explicit block was found
        if not reasoning or reasoning == "Forensic inspection of codebase pattern.":
            if loc_comment:
                reasoning = loc_comment
            else:
                # Let's split by double newlines to find paragraphs
                paragraphs = description.split('\n\n')
                matched_p = None
                for p in paragraphs:
                    p_clean = p.strip()
                    if not p_clean:
                        continue
                    # Check for classic slop keywords in the paragraph
                    if re.search(r'\b(?:this reads as|this appears to|this is the canonical|this is a classic signature|a human engineer|ai-generated|likely slop|why likely slop|cargo-cult|signature of incremental|textbook)\b', p_clean, re.IGNORECASE):
                        matched_p = p
                        reasoning = p_clean
                        break
                
                # Remove the extracted reasoning paragraph from the description so we don't display it twice
                if matched_p:
                    description = description.replace(matched_p, "").strip()
                else:
                    # If no matching reasoning paragraph was found, generate a highly contextual details explanation
                    t_lower = title.lower()
                    if any(w in t_lower for w in ["duplicate", "copy-paste", "redundant", "verbatim"]):
                        reasoning = "Code duplication is a classic signature of AI-assisted scaffolding. The agent generated components or helpers locally in multiple places rather than refactoring or establishing a proper shared utility module, leading to maintenance overhead and potential code drift."
                    elif any(w in t_lower for w in ["unused", "dead", "unreachable", "eslint-disable"]):
                        reasoning = "Unused files, dead imports, and suppressed linter rules indicate over-generation or cargo-culting. The agent pre-stocked dependencies or hooks it 'might need' and silenced warnings rather than keeping the codebase lean and clean."
                    elif any(w in t_lower for w in ["test", "tests", "testing"]):
                        reasoning = "A lack of unit tests or test setups is a common gap in AI-generated scaffolds. The agent focuses on emitting functional-looking UI code while omitting test-driven verification, leaving the codebase vulnerable to silent regressions."
                    elif any(w in t_lower for w in ["require", "dynamic"]):
                        reasoning = "The use of dynamic require statements in ES module contexts is an obsolete template pattern. It bypasses compile-time safety and type checking, creating a potential runtime failure point."
                    else:
                        reasoning = "Forensic inspection of the codebase identified template-like code structures, speculative scaffolding, or redundant blocks that deviate from clean engineering best practices."

        description = re.sub(r'\n+', '\n', description).strip()
        
        findings.append({
            "id": fid,
            "title": title,
            "likelihood": likelihood,
            "location": location,
            "description": description,
            "code_lang": code_lang,
            "code_snippet": code_snippet,
            "reasoning": reasoning,
            "risk": risk
        })
        
    # Relative rank fallback for completely unknown/default severities
    for idx, f in enumerate(findings):
        if f['likelihood'] == "Unknown":
            if len(findings) > 0:
                rank_ratio = idx / len(findings)
                if rank_ratio < 0.35:
                    f['likelihood'] = "High"
                elif rank_ratio < 0.7:
                    f['likelihood'] = "Medium"
                else:
                    f['likelihood'] = "Low"
            else:
                f['likelihood'] = "Medium"
                
        if f['risk'] == "Unknown":
            if len(findings) > 0:
                rank_ratio = idx / len(findings)
                if rank_ratio < 0.35:
                    f['risk'] = "High"
                elif rank_ratio < 0.7:
                    f['risk'] = "Medium"
                else:
                    f['risk'] = "Low"
            else:
                f['risk'] = "Medium"
                
    return findings

def map_severity(level_str, default="Low"):
    l = level_str.strip().lower()
    if not l or l == "unknown" or l == "none":
        return default
    
    # Try parsing numeric values / percentages / scores
    # Match digit patterns like 88%, 9/10, 8, etc.
    num_match = re.search(r'(\d+(?:\.\d+)?)\s*(?:%|/10)?', l)
    if num_match:
        val = float(num_match.group(1))
        # Check if it was formatted as x/10
        if '/10' in l:
            if val >= 7:
                return "High"
            elif val >= 4:
                return "Medium"
            else:
                return "Low"
        elif '%' in l or val > 10:
            if val >= 75:
                return "High"
            elif val >= 40:
                return "Medium"
            else:
                return "Low"
        else:
            # Single digit or float without scale. Guess based on value:
            if val >= 7:
                return "High"
            elif val >= 4:
                return "Medium"
            else:
                return "Low"

    # Keywords
    if "critical" in l or "severe" in l or "very high" in l or "high" in l or "p0" in l or "p1" in l:
        return "High"
    elif "moderate-high" in l or "medium-high" in l or "mod-high" in l:
        return "High"
    elif "medium" in l or "moderate" in l or "mod" in l or "p2" in l:
        return "Medium"
    elif "low" in l or "minor" in l or "negligible" in l or "p3" in l or "p4" in l:
        return "Low"
    else:
        return default

def map_impact(impact_str, default="Medium"):
    l = impact_str.strip().lower()
    if not l or l == "unknown" or l == "none":
        return default
    
    # Try parsing numeric values / percentages / scores
    num_match = re.search(r'(\d+(?:\.\d+)?)\s*(?:%|/10)?', l)
    if num_match:
        val = float(num_match.group(1))
        if '/10' in l:
            if val >= 7:
                return "High"
            elif val >= 4:
                return "Medium"
            else:
                return "Low"
        elif '%' in l or val > 10:
            if val >= 75:
                return "High"
            elif val >= 40:
                return "Medium"
            else:
                return "Low"
        else:
            if val >= 7:
                return "High"
            elif val >= 4:
                return "Medium"
            else:
                return "Low"

    # Keywords
    if "critical" in l or "severe" in l or "very high" in l or "high" in l or "p0" in l or "p1" in l:
        return "High"
    elif "moderate-high" in l or "medium-high" in l or "mod-high" in l:
        return "High"
    elif "medium" in l or "moderate" in l or "mod" in l or "p2" in l:
        return "Medium"
    elif "low" in l or "minor" in l or "negligible" in l or "p3" in l or "p4" in l:
        return "Low"
    else:
        # Check if the text contains any of these keywords in a sentence
        if any(w in l for w in ["critical", "severe", "crash", "fails typescript", "typescript compilation failure", "broken", "major feature", "system breakage"]):
            return "High"
        elif any(w in l for w in ["warning", "medium", "moderate", "redundancy", "redundant", "duplicate", "redundant", "inconsistency"]):
            return "Medium"
        elif any(w in l for w in ["low", "minor", "hygiene", "cosmetic", "scattered", "cleanup", "process"]):
            return "Low"
        return default

def get_next_audit_info(proj_root, is_test_report, category_subfolder=None):
    """
    Scans <proj_root>/Audit Reports/<subfolder> for existing sequential html files.
    Returns a tuple: (next_output_path, previous_score, next_index, previous_findings)
    previous_findings is a list of {"id": "F1", "title": "..."} dicts from the last report, or [].
    """
    base_audit_dir = os.path.join(proj_root, "Audit Reports")
    if category_subfolder:
        sub_folder = category_subfolder
        if category_subfolder == "Test Case":
            prefix = "test_case_audit_"
        elif category_subfolder == "AI Slop":
            prefix = "ai_slop_audit_"
        else:
            prefix = category_subfolder.lower().replace(" ", "_") + "_"
    elif is_test_report:
        sub_folder = "Test Case"
        prefix = "test_case_audit_"
    else:
        sub_folder = "AI Slop"
        prefix = "ai_slop_audit_"
    target_dir = os.path.join(base_audit_dir, sub_folder)
    os.makedirs(target_dir, exist_ok=True)
    
    pattern = re.compile(rf"^{re.escape(prefix)}(\d+)\.html$", re.IGNORECASE)
    
    existing = []
    if os.path.exists(target_dir):
        for fname in os.listdir(target_dir):
            m = pattern.match(fname)
            if m:
                idx = int(m.group(1))
                existing.append((idx, os.path.join(target_dir, fname)))
                
    if not existing:
        next_idx = 1
        next_path = os.path.join(target_dir, f"{prefix}1.html")
        return next_path, None, 1, []
        
    existing.sort(key=lambda x: x[0])
    last_idx, last_path = existing[-1]
    next_idx = last_idx + 1
    next_path = os.path.join(target_dir, f"{prefix}{next_idx}.html")
    
    previous_score = None
    previous_findings = []
    try:
        if os.path.exists(last_path):
            with open(last_path, "r", encoding="utf-8") as pf:
                prev_html = pf.read()
                # Extract score
                m_score = re.search(r'class="score-value"[^>]*>\s*(\d+)', prev_html)
                if m_score:
                    previous_score = int(m_score.group(1))
                # Extract finding IDs, titles, and locations from finding cards
                # Split by class="finding-card to handle each card individually
                card_parts = prev_html.split('class="finding-card')
                for part in card_parts[1:]:
                    id_m = re.search(r'<span class="finding-id">([^<]+)</span>', part)
                    title_m = re.search(r'<h3 class="finding-title">([^<]+)</h3>', part)
                    loc_m = re.search(r'class="meta-value font-mono"[^>]*>\s*([^<]+)', part)
                    
                    if id_m and title_m:
                        fid = id_m.group(1).strip()
                        ftitle = html.unescape(title_m.group(1).strip())
                        floc = html.unescape(loc_m.group(1).strip()) if loc_m else ""
                        previous_findings.append({
                            "id": fid,
                            "title": ftitle,
                            "location": floc
                        })
    except Exception:
        previous_score = None
        previous_findings = []
        
    return next_path, previous_score, next_idx, previous_findings

def stem_word(w):
    w = w.lower().strip()
    if len(w) > 4:
        if w.endswith('ies'):
            w = w[:-3] + 'y'
        elif w.endswith('es') and not w.endswith('ees'):
            w = w[:-2]
        elif w.endswith('s') and not w.endswith('ss'):
            w = w[:-1]
        elif w.endswith('ing'):
            w = w[:-3]
        elif w.endswith('ed'):
            w = w[:-2]
    return w

def extract_filename(loc):
    if not loc:
        return ""
    # Normalize path and line numbers
    clean = re.sub(r'[\s\d:,\-—\(\)]+$', '', loc.strip())
    clean = re.sub(r'\(lines.*?\)', '', clean, flags=re.IGNORECASE)
    clean = clean.split(':')[0].strip()
    return os.path.basename(clean).strip().lower()

def title_similarity(t1, t2):
    words1 = {stem_word(w) for w in re.findall(r'\b\w{3,}\b', t1)}
    words2 = {stem_word(w) for w in re.findall(r'\b\w{3,}\b', t2)}
    if not words1 or not words2:
        return 0.0
    intersection = words1.intersection(words2)
    union = words1.union(words2)
    return len(intersection) / len(union)

def is_similar_finding(f_prev, f_curr):
    t_prev = f_prev.get("title", "")
    t_curr = f_curr.get("title", "")
    
    # Clean F1 / F2 prefixes from previous title just in case
    t_prev = re.sub(r'^\[(?:Finding|F)?\s*\d+\]\s*', '', t_prev, flags=re.IGNORECASE).strip()
    t_prev = re.sub(r'^F?\d+[\.\s\-:]+\s*', '', t_prev, flags=re.IGNORECASE).strip()
    t_curr = re.sub(r'^\[(?:Finding|F)?\s*\d+\]\s*', '', t_curr, flags=re.IGNORECASE).strip()
    t_curr = re.sub(r'^F?\d+[\.\s\-:]+\s*', '', t_curr, flags=re.IGNORECASE).strip()

    if t_prev.strip().lower() == t_curr.strip().lower():
        return True
        
    sim = title_similarity(t_prev, t_curr)
    if sim >= 0.45:
        return True
        
    # Check locations file sharing
    loc_prev = f_prev.get("location", "")
    loc_curr = f_curr.get("location", "")
    
    file_prev = extract_filename(loc_prev)
    file_curr = extract_filename(loc_curr)
    
    files_prev = set(re.findall(r'\b\w+\.(?:tsx?|json|jsx?|py|dart|go|c|cpp|h)\b', loc_prev.lower()))
    files_curr = set(re.findall(r'\b\w+\.(?:tsx?|json|jsx?|py|dart|go|c|cpp|h)\b', loc_curr.lower()))
    
    shared_files = files_prev.intersection(files_curr)
    has_shared_file = len(shared_files) > 0 or (file_prev and file_curr and file_prev == file_curr)
    
    if has_shared_file:
        # If they share a file, title similarity threshold is much lower
        if sim >= 0.15:
            return True
        # Or if both contain specific keywords like "test", "mock", "lint", "typecheck"
        keywords = {stem_word(kw) for kw in ["test", "mock", "lint", "type", "error", "coverage", "untested", "gate"]}
        prev_kws = {stem_word(w) for w in re.findall(r'\w+', t_prev.lower()) if stem_word(w) in keywords}
        curr_kws = {stem_word(w) for w in re.findall(r'\w+', t_curr.lower()) if stem_word(w) in keywords}
        if prev_kws.intersection(curr_kws):
            return True
            
    return False

def generate_html_report(title, metadata, disclaimer, sections, output_path, previous_score=None, previous_findings=None, next_idx=1):
    if previous_findings is None:
        previous_findings = []
    findings_body = ""
    overview_body = ""
    observations_body = ""
    stable_areas_body = ""
    remediation_body = ""
    
    for key, val in sections.items():
        k_lower = key.lower()
        if any(w in k_lower for w in ["detailed findings", "ranked problems", "findings", "problems", "vulnerabilities", "issues", "audit findings"]):
            findings_body = val
        elif any(w in k_lower for w in ["remediation", "ai remediation"]):
            remediation_body = val
        elif any(w in k_lower for w in ["pattern observation", "pattern", "observations"]):
            observations_body = val
        elif any(w in k_lower for w in ["stable", "intentional", "areas"]):
            stable_areas_body = val
        elif any(w in k_lower for w in ["executive overview", "overview", "system", "how the system", "rating", "summary", "intro"]):
            if len(val.strip()) > 20:
                overview_body += val + "\n\n"
    proj_dir = os.path.abspath(os.path.dirname(output_path))
    if "Audit Reports" in proj_dir:
        proj_dir = proj_dir.split("Audit Reports")[0].rstrip("/\\")
    dir_name = os.path.basename(proj_dir)
    is_test_report = "test_case" in (title or "").lower() or "test case" in (title or "").lower() or "test_case" in os.path.basename(output_path).lower() or "test-case" in os.path.basename(output_path).lower()
    output_fn = os.path.basename(output_path).lower()
    raw_title = title if title and title != "AI Slop Audit Report" else ""
    raw_title_clean = re.sub(r'\s*#\d+\s*$', '', raw_title).strip()

    all_bullets = []
    if observations_body:
        all_bullets.extend([b.strip() for b in re.split(r'\n(?=\s*[-*+]\s+|\s*\d+\.\s+)', observations_body) if b.strip() and any(b.strip().startswith(c) for c in ['-', '*', '+', '1', '2', '3', '4', '5', '6'])])
    if stable_areas_body:
        all_bullets.extend([b.strip() for b in re.split(r'\n(?=\s*[-*+]\s+|\s*\d+\.\s+)', stable_areas_body) if b.strip() and any(b.strip().startswith(c) for c in ['-', '*', '+', '1', '2', '3', '4', '5', '6'])])

    if len(all_bullets) >= 4:
        mid = min(3, len(all_bullets) // 2)
        observations_body = "\n".join(all_bullets[:mid])
        stable_areas_body = "\n".join(all_bullets[mid:])
    else:
        if not observations_body or len(observations_body.strip()) < 10:
            if "performance" in (title or "").lower() or "performance" in output_path.lower():
                observations_body = (
                    "- Main thread event handling and synchronous computation overhead in hot loop paths.\n"
                    "- State mutation propagation, unmemoized calculations, and component re-render cascading.\n"
                    "- Data serialization, unindexed query lookups, and unthrottled memory allocations."
                )
            elif "security" in (title or "").lower() or "security" in output_path.lower():
                observations_body = (
                    "- API route authentication guards, role-based authorization, and token verification checks.\n"
                    "- Credential handling, environment variable loading, and secret leakage prevention.\n"
                    "- Subprocess invocations, input validation, and sandbox perimeter safety."
                )
            elif is_test_report:
                observations_body = (
                    "- Unit test coverage distribution across core domain logic and edge conditions.\n"
                    "- Mock configuration patterns, native module isolation, and assertion rigor.\n"
                    "- Test execution reliability, async cleanup handling, and regression prevention."
                )
            else:
                observations_body = (
                    "- Architectural modularity, separation of concerns, and component encapsulation.\n"
                    "- Code reuse patterns, shared utility abstraction, and state management flow.\n"
                    "- Error boundary handling, type safety consistency, and dependency hygiene."
                )
        if not stable_areas_body or len(stable_areas_body.strip()) < 10:
            if "performance" in (title or "").lower() or "performance" in output_path.lower():
                stable_areas_body = (
                    "- Module-scope static declarations and bounded state allocations across active sessions.\n"
                    "- Efficient async scheduling and streamlined network request handlers.\n"
                    "- Static UI views, asset caching, and deterministic configuration lookups."
                )
            elif "security" in (title or "").lower() or "security" in output_path.lower():
                stable_areas_body = (
                    "- Hardened authentication middleware and sanitized database parameterization.\n"
                    "- Safe environment configuration isolation and encrypted storage primitives.\n"
                    "- Controlled network egress routing and strict CORS policy definitions."
                )
            elif is_test_report:
                stable_areas_body = (
                    "- Isolated unit test suites for deterministic domain logic calculations.\n"
                    "- Clean mock factories and consistent test fixture setup and teardown.\n"
                    "- Strong type assertions and automated continuous integration test gates."
                )
            else:
                stable_areas_body = (
                    "- Core business logic modules with high cohesion and clean dependency graphs.\n"
                    "- Consistent type definitions, strict interfaces, and lint rule compliance.\n"
                    "- Robust error handling pathways and well-structured configuration management."
                )

    if stable_areas_body:
        stable_areas_body = re.sub(
            r'-+\s*\*End of report.*?\*',
            '',
            stable_areas_body,
            flags=re.IGNORECASE | re.DOTALL
        )
        stable_areas_body = re.sub(
            r'\*End of report.*?\*',
            '',
            stable_areas_body,
            flags=re.IGNORECASE | re.DOTALL
        )
        stable_areas_body = stable_areas_body.strip()
        if stable_areas_body.endswith('---'):
            stable_areas_body = stable_areas_body[:-3].strip()

    # Force current date and time (when audit stopped)
    current_date_str = datetime.datetime.now().strftime("%B %d, %Y at %I:%M:%S %p")
    date_key = "Date"
    for k in list(metadata.keys()):
        if "date" in k.lower():
            date_key = k
            break
    metadata[date_key] = current_date_str

    if "performance" in output_fn or "performance" in raw_title_clean.lower():
        report_title = "Performance Audit Report"
    elif "security" in output_fn or "security" in raw_title_clean.lower():
        report_title = "Security Audit Report"
    elif "frontend" in output_fn or "frontend" in raw_title_clean.lower():
        report_title = "Frontend Slop Audit Report"
    elif "backend" in output_fn or "backend" in raw_title_clean.lower():
        report_title = "Backend Slop Audit Report"
    elif "test" in output_fn or "test" in raw_title_clean.lower():
        report_title = "Test Case Audit Report"
    elif "architecture" in output_fn or "arch" in raw_title_clean.lower():
        report_title = "Architecture Audit Report"
    elif "mobile" in output_fn or "mobile" in raw_title_clean.lower():
        report_title = "Mobile UX Audit Report"
    elif "website" in output_fn or "website" in raw_title_clean.lower() or "ux" in raw_title_clean.lower():
        report_title = "Website UX Audit Report"
    elif "gaps" in output_fn or "gaps" in raw_title_clean.lower():
        report_title = "UI Gaps Audit Report"
    elif "chart" in output_fn or "chart" in raw_title_clean.lower():
        report_title = "Chart Validation Audit Report"
    elif "hygiene" in output_fn or "hygiene" in raw_title_clean.lower():
        report_title = "Hygiene Audit Report"
    elif "loop" in output_fn or "loop" in raw_title_clean.lower():
        report_title = "Loop Audit Report"
    elif "prompt" in output_fn or "prompt" in raw_title_clean.lower():
        report_title = "Prompt Audit Report"
    elif raw_title_clean:
        report_title = raw_title_clean if "Audit" in raw_title_clean or "Report" in raw_title_clean else f"{raw_title_clean} Audit Report"
    else:
        report_title = "AI Slop Audit Report"

    display_title = f"{report_title} #{next_idx} — {dir_name}" if next_idx > 1 else f"{report_title} — {dir_name}"

    # Parse findings
    findings = parse_findings(findings_body)

    # Enforce strict findings cap at 10 max
    if len(findings) > 10:
        findings = findings[:10]

    # Calculate resolved vs current findings if previous_findings exists
    resolved_findings = []
    if previous_findings:
        for pf in previous_findings:
            found_similar = False
            for f in findings:
                if is_similar_finding(pf, f):
                    found_similar = True
                    break
            if not found_similar:
                resolved_findings.append(pf)

    new_metadata = {}
    for k, v in metadata.items():
        new_metadata[k] = v
        if k == date_key:
            new_metadata["Audited Directory"] = proj_dir
            if next_idx > 1 and previous_score is not None:
                new_metadata["Previous Report"] = f"Report #{next_idx - 1}"
                new_metadata["Issues Resolved"] = f"{len(resolved_findings)} of {len(previous_findings)} previous findings fixed"
    metadata = new_metadata

    # Sort findings by severity: High first, then Medium, then Low
    def get_severity_rank(f):
        sev = map_severity(f['likelihood'])
        if sev == "High":
            return 0
        elif sev == "Medium":
            return 1
        else:
            return 2
            
    findings.sort(key=get_severity_rank)

    # Re-map findings sequentially by index to guarantee F1, F2, F3 etc. and resolve missing severities / locations
    for idx, f in enumerate(findings):
        f['id'] = f"F{idx + 1}"
        f['title'] = clean_finding_title(f['title'])
        loc_clean, loc_cmt = clean_finding_location(f.get('location', ''))
        f['location'] = loc_clean
        if loc_cmt and (not f.get('reasoning') or f['reasoning'] == "Forensic inspection of codebase pattern."):
            f['reasoning'] = loc_cmt
        
        if f['likelihood'] == "Unknown":
            body_text = f['description'] + "\n" + f['reasoning'] + "\n" + f['risk']
            m_lik = re.search(r'(?:\*\*|\b)(?:Likelihood|Severity)\b(?:\*\*|)\s*:\s*\*?\*?([A-Za-z\-]+)', body_text, re.IGNORECASE)
            if m_lik:
                f['likelihood'] = m_lik.group(1).strip()

        if not f['location']:
            body_text = f['description'] + "\n" + f['reasoning'] + "\n" + f['risk']
            file_matches = re.findall(r'[\w\-./\\]+\.(?:tsx|ts|jsx|js|json)(?::\d+(?:-\d+)?)?', body_text, re.IGNORECASE)
            unique_files = []
            for fm in file_matches:
                fm_clean = fm.strip('.,()`":;')
                if fm_clean and not fm_clean.startswith('<') and not fm_clean.endswith('>') and fm_clean not in unique_files:
                    unique_files.append(fm_clean)
            if unique_files:
                f['location'] = ', '.join(unique_files)

    def clean_trailing_separators(html_str):
        if not html_str:
            return html_str
        html_str = re.sub(r'^(?:\s*<p>\s*---\s*</p>|\s*<hr\s*/?>)+', '', html_str)
        html_str = re.sub(r'(?:\s*<p>\s*---\s*</p>|\s*<hr\s*/?>)+\s*$', '', html_str)
        return html_str.strip()

    observations = clean_trailing_separators(markdown_to_html(observations_body))
    stable_areas = clean_trailing_separators(markdown_to_html(stable_areas_body))

    high_count = sum(1 for f in findings if map_severity(f['likelihood']) == "High")
    medium_count = sum(1 for f in findings if map_severity(f['likelihood']) == "Medium")
    low_count = sum(1 for f in findings if map_severity(f['likelihood']) == "Low")

    is_slop_report = "slop" in (report_title + " " + output_fn).lower()
    is_higher_better = not is_slop_report

    if is_higher_better:
        deductions = (high_count * 1.2) + (medium_count * 0.5) + (low_count * 0.2)
        score_value = max(1, min(10, int(round(10.0 - deductions))))
        if score_value >= 7:
            verdict = "Low"
        elif score_value >= 4:
            verdict = "Medium"
        else:
            verdict = "High"
    else:
        points = (high_count * 1.2) + (medium_count * 0.5) + (low_count * 0.2)
        score_value = min(10, max(1, int(round(points))))
        if score_value <= 3:
            verdict = "Low"
        elif score_value <= 6:
            verdict = "Medium"
        else:
            verdict = "High"

    extracted_score = score_value

    r_lower = (report_title + " " + output_fn).lower()

    if "performance" in r_lower:
        score_card_title = "Performance Score"
        score_card_value = str(extracted_score)
        score_card_color = "#10b981" if extracted_score >= 7 else ("#f97316" if extracted_score >= 4 else "#ef4444")
        score_tooltip_html = """
        <div style="font-weight: 700; margin-bottom: 4px; border-bottom: 1px solid rgba(255,255,255,0.2); padding-bottom: 3px;">Performance Score Color Coding</div>
        <div style="display: flex; flex-direction: column; gap: 3px; font-size: 11px;">
            <div><span style="color: #10b981; font-weight: 700;">● 7–10:</span> High Performance (Good)</div>
            <div><span style="color: #f97316; font-weight: 700;">● 4–6:</span> Medium Performance</div>
            <div><span style="color: #ef4444; font-weight: 700;">● 0–3:</span> Low Performance (Bottlenecks)</div>
        </div>
        """
        score_explanation_note = "The **Performance Score (0–10)** assesses runtime throughput, event loop responsiveness, query efficiency, memory utilization, and algorithmic performance across the codebase."
    elif "security" in r_lower:
        score_card_title = "Security Posture Score"
        score_card_value = str(extracted_score)
        score_card_color = "#10b981" if extracted_score >= 7 else ("#f97316" if extracted_score >= 4 else "#ef4444")
        score_tooltip_html = """
        <div style="font-weight: 700; margin-bottom: 4px; border-bottom: 1px solid rgba(255,255,255,0.2); padding-bottom: 3px;">Security Score Color Coding</div>
        <div style="display: flex; flex-direction: column; gap: 3px; font-size: 11px;">
            <div><span style="color: #10b981; font-weight: 700;">● 7–10:</span> High Security Posture (Good)</div>
            <div><span style="color: #f97316; font-weight: 700;">● 4–6:</span> Medium Security Posture</div>
            <div><span style="color: #ef4444; font-weight: 700;">● 0–3:</span> Low Security Posture (Vulnerabilities)</div>
        </div>
        """
        score_explanation_note = "The **Security Posture Score (0–10)** assesses token safety, API authentication guards, credential exposure risk, OWASP Top 10 vulnerabilities, and sandbox security posture."
    elif is_test_report or "test_case" in r_lower or "test case" in r_lower:
        score_card_title = "Test Quality Score"
        score_card_value = str(extracted_score)
        score_card_color = "#10b981" if extracted_score >= 7 else ("#f97316" if extracted_score >= 4 else "#ef4444")
        score_tooltip_html = """
        <div style="font-weight: 700; margin-bottom: 4px; border-bottom: 1px solid rgba(255,255,255,0.2); padding-bottom: 3px;">Test Quality Color Ratings</div>
        <div style="display: flex; flex-direction: column; gap: 3px; font-size: 11px;">
            <div><span style="color: #10b981; font-weight: 700;">● 7–10:</span> High Quality (Good)</div>
            <div><span style="color: #f97316; font-weight: 700;">● 4–6:</span> Medium Quality</div>
            <div><span style="color: #ef4444; font-weight: 700;">● 0–3:</span> Low Quality (Poor)</div>
        </div>
        """
        score_explanation_note = "The **Test Quality Score (0–10)** assesses test coverage depth, mock configurations, edge case handling, and test suite maturity across the codebase."
    elif "website" in r_lower or "ux" in r_lower:
        score_card_title = "UX Score"
        score_card_value = str(extracted_score)
        score_card_color = "#10b981" if extracted_score >= 7 else ("#f97316" if extracted_score >= 4 else "#ef4444")
        score_tooltip_html = """
        <div style="font-weight: 700; margin-bottom: 4px; border-bottom: 1px solid rgba(255,255,255,0.2); padding-bottom: 3px;">UX Score Color Coding</div>
        <div style="display: flex; flex-direction: column; gap: 3px; font-size: 11px;">
            <div><span style="color: #10b981; font-weight: 700;">● 7–10:</span> High UX Quality (Intuitive)</div>
            <div><span style="color: #f97316; font-weight: 700;">● 4–6:</span> Medium UX Quality</div>
            <div><span style="color: #ef4444; font-weight: 700;">● 0–3:</span> Low UX Quality (UX Defects)</div>
        </div>
        """
        score_explanation_note = "The **UX Score (0–10)** assesses website navigation flows, accessibility standards, responsive layouts, and user feedback mechanisms across the interface."
    elif "mobile" in r_lower:
        score_card_title = "Mobile UI Score"
        score_card_value = str(extracted_score)
        score_card_color = "#10b981" if extracted_score >= 7 else ("#f97316" if extracted_score >= 4 else "#ef4444")
        score_tooltip_html = """
        <div style="font-weight: 700; margin-bottom: 4px; border-bottom: 1px solid rgba(255,255,255,0.2); padding-bottom: 3px;">Mobile UI Score Color Coding</div>
        <div style="display: flex; flex-direction: column; gap: 3px; font-size: 11px;">
            <div><span style="color: #10b981; font-weight: 700;">● 7–10:</span> High Mobile UI Quality (Polished)</div>
            <div><span style="color: #f97316; font-weight: 700;">● 4–6:</span> Medium Mobile UI Quality</div>
            <div><span style="color: #ef4444; font-weight: 700;">● 0–3:</span> Low Mobile UI Quality (Touch/Layout Defects)</div>
        </div>
        """
        score_explanation_note = "The **Mobile UI Score (0–10)** assesses mobile touch target sizes (≥44pt), viewport density, gesture interactions, and responsive mobile screen layouts."
    elif "web_app" in r_lower or "web app" in r_lower:
        score_card_title = "Web UI Score"
        score_card_value = str(extracted_score)
        score_card_color = "#10b981" if extracted_score >= 7 else ("#f97316" if extracted_score >= 4 else "#ef4444")
        score_tooltip_html = """
        <div style="font-weight: 700; margin-bottom: 4px; border-bottom: 1px solid rgba(255,255,255,0.2); padding-bottom: 3px;">Web UI Score Color Coding</div>
        <div style="display: flex; flex-direction: column; gap: 3px; font-size: 11px;">
            <div><span style="color: #10b981; font-weight: 700;">● 7–10:</span> High Web UI Quality (Polished)</div>
            <div><span style="color: #f97316; font-weight: 700;">● 4–6:</span> Medium Web UI Quality</div>
            <div><span style="color: #ef4444; font-weight: 700;">● 0–3:</span> Low Web UI Quality (Layout Defects)</div>
        </div>
        """
        score_explanation_note = "The **Web UI Score (0–10)** assesses web application layout polish, visual hierarchy, typography, contrast, and responsive desktop/tablet breakpoints."
    elif "ui_gap" in r_lower or "ui gap" in r_lower or "gap" in r_lower:
        score_card_title = "UI Gap Score"
        score_card_value = str(extracted_score)
        score_card_color = "#10b981" if extracted_score >= 7 else ("#f97316" if extracted_score >= 4 else "#ef4444")
        score_tooltip_html = """
        <div style="font-weight: 700; margin-bottom: 4px; border-bottom: 1px solid rgba(255,255,255,0.2); padding-bottom: 3px;">UI Gap Score Color Coding</div>
        <div style="display: flex; flex-direction: column; gap: 3px; font-size: 11px;">
            <div><span style="color: #10b981; font-weight: 700;">● 7–10:</span> Complete UI State Handling (Robust)</div>
            <div><span style="color: #f97316; font-weight: 700;">● 4–6:</span> Moderate UI Gaps</div>
            <div><span style="color: #ef4444; font-weight: 700;">● 0–3:</span> High UI Gaps (Missing Error/Empty States)</div>
        </div>
        """
        score_explanation_note = "The **UI Gap Score (0–10)** assesses UI state completeness, loading indicators, error boundaries, empty states, and component coupling across the frontend."
    elif "chart" in r_lower:
        score_card_title = "Chart Score"
        score_card_value = str(extracted_score)
        score_card_color = "#10b981" if extracted_score >= 7 else ("#f97316" if extracted_score >= 4 else "#ef4444")
        score_tooltip_html = """
        <div style="font-weight: 700; margin-bottom: 4px; border-bottom: 1px solid rgba(255,255,255,0.2); padding-bottom: 3px;">Chart Score Color Coding</div>
        <div style="display: flex; flex-direction: column; gap: 3px; font-size: 11px;">
            <div><span style="color: #10b981; font-weight: 700;">● 7–10:</span> Truthful & Clear Visualizations</div>
            <div><span style="color: #f97316; font-weight: 700;">● 4–6:</span> Moderate Visualization Issues</div>
            <div><span style="color: #ef4444; font-weight: 700;">● 0–3:</span> Misleading / Flawed Charts</div>
        </div>
        """
        score_explanation_note = "The **Chart Score (0–10)** assesses visualization suitability, axis scaling honesty, metric aggregation accuracy, and responsive chart presentation."
    elif "backend" in r_lower and "slop" in r_lower:
        score_card_title = "Backend Slop Score"
        score_card_value = str(extracted_score)
        score_card_color = "#ef4444" if extracted_score >= 7 else ("#f97316" if extracted_score >= 4 else "#10b981")
        score_tooltip_html = """
        <div style="font-weight: 700; margin-bottom: 4px; border-bottom: 1px solid rgba(255,255,255,0.2); padding-bottom: 3px;">Backend Slop Score Color Coding</div>
        <div style="display: flex; flex-direction: column; gap: 3px; font-size: 11px;">
            <div><span style="color: #10b981; font-weight: 700;">● 0–3:</span> Low Slop (Clean Backend)</div>
            <div><span style="color: #f97316; font-weight: 700;">● 4–6:</span> Medium Slop</div>
            <div><span style="color: #ef4444; font-weight: 700;">● 7–10:</span> High Slop (Dead Routes / Over-Abstraction)</div>
        </div>
        """
        score_explanation_note = "The **Backend Slop Score (0–10)** assesses backend code for dead routes, over-engineering, unnecessary abstractions, and duplicated logic."
    elif "frontend" in r_lower and "slop" in r_lower:
        score_card_title = "Frontend Slop Score"
        score_card_value = str(extracted_score)
        score_card_color = "#ef4444" if extracted_score >= 7 else ("#f97316" if extracted_score >= 4 else "#10b981")
        score_tooltip_html = """
        <div style="font-weight: 700; margin-bottom: 4px; border-bottom: 1px solid rgba(255,255,255,0.2); padding-bottom: 3px;">Frontend Slop Score Color Coding</div>
        <div style="display: flex; flex-direction: column; gap: 3px; font-size: 11px;">
            <div><span style="color: #10b981; font-weight: 700;">● 0–3:</span> Low Slop (Clean UI State)</div>
            <div><span style="color: #f97316; font-weight: 700;">● 4–6:</span> Medium Slop</div>
            <div><span style="color: #ef4444; font-weight: 700;">● 7–10:</span> High Slop (Re-renders / Prop-Drilling)</div>
        </div>
        """
        score_explanation_note = "The **Frontend Slop Score (0–10)** assesses frontend code for unneeded re-renders, prop-drilling, dead components, and over-abstracted hooks."
    else:
        score_card_title = "Overall Slop Score"
        score_card_value = str(extracted_score)
        score_card_color = "#ef4444" if extracted_score >= 7 else ("#f97316" if extracted_score >= 4 else "#10b981")
        score_tooltip_html = """
        <div style="font-weight: 700; margin-bottom: 4px; border-bottom: 1px solid rgba(255,255,255,0.2); padding-bottom: 3px;">Slop Score Color Coding</div>
        <div style="display: flex; flex-direction: column; gap: 3px; font-size: 11px;">
            <div><span style="color: #10b981; font-weight: 700;">● 0–3:</span> Low Slop (Clean)</div>
            <div><span style="color: #f97316; font-weight: 700;">● 4–6:</span> Medium Slop</div>
            <div><span style="color: #ef4444; font-weight: 700;">● 7–10:</span> High Slop (Severe)</div>
        </div>
        """
        score_explanation_note = "The **Overall Slop Score (0–10)** assesses how strongly codebase patterns resemble AI-generated speculative scaffoldings, copy-paste duplication, cargo-cult code, or dead imports."

    disclaimer_html = f'<div class="score-explanation-note">{markdown_to_html(score_explanation_note)}</div>'
    
    # Score Card HTML - Side-by-side when previous score exists
    if previous_score is not None and next_idx > 1:
        if "performance" in report_title.lower() or "security" in report_title.lower() or is_test_report:
            prev_score_color = "#10b981" if previous_score >= 7 else ("#f97316" if previous_score >= 4 else "#ef4444")
        else:
            prev_score_color = "#ef4444" if previous_score >= 7 else ("#f97316" if previous_score >= 4 else "#10b981")
            
        score_card_wrapper_html = f"""
        <div class="score-card-wrapper">
            <div class="score-cards-row">
                <div class="score-card prev">
                    <div class="score-title">Previous Score</div>
                    <div class="score-value" style="color: {prev_score_color};">{previous_score}<span class="score-scale">/10</span></div>
                </div>
                <div class="score-arrow">→</div>
                <div class="score-card current" style="border-color: {score_card_color}; box-shadow: 0 0 0 3px rgba(16, 185, 129, 0.12), var(--shadow-md);">
                    <div class="score-title">
                        <span>Current Score</span>
                        <div class="info-tooltip">
                            <svg viewBox="0 0 24 24" width="12" height="12" stroke="currentColor" stroke-width="2.5" fill="none" stroke-linecap="round" stroke-linejoin="round" style="cursor: help; color: var(--text-muted);"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="16" x2="12" y2="12"></line><line x1="12" y1="8" x2="12.01" y2="8"></line></svg>
                            <span class="tooltip-text">{score_tooltip_html}</span>
                        </div>
                    </div>
                    <div class="score-value" style="color: {score_card_color};">{extracted_score}<span class="score-scale">/10</span></div>
                </div>
            </div>
        </div>
        """
    else:
        score_card_wrapper_html = f"""
        <div class="score-card-wrapper">
            <div class="score-card">
                <div class="score-title">
                    <span>{score_card_title}</span>
                    <div class="info-tooltip">
                        <svg viewBox="0 0 24 24" width="12" height="12" stroke="currentColor" stroke-width="2.5" fill="none" stroke-linecap="round" stroke-linejoin="round" style="cursor: help; color: var(--text-muted);"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="16" x2="12" y2="12"></line><line x1="12" y1="8" x2="12.01" y2="8"></line></svg>
                        <span class="tooltip-text">{score_tooltip_html}</span>
                    </div>
                </div>
                <div class="score-value" style="color: {score_card_color};">{extracted_score}<span class="score-scale">/10</span></div>
            </div>
        </div>
        """

    # Sidebar Report History section link
    report_history_html = ""
    if next_idx > 1 and previous_score is not None:
        fn_basename = os.path.basename(output_path)
        prefix_match = re.match(r"^(.+_)(\d+)\.html$", fn_basename, re.IGNORECASE)
        if prefix_match:
            prev_file_name = f"{prefix_match.group(1)}{next_idx - 1}.html"
        else:
            prev_file_name = f"test_case_audit_{next_idx - 1}.html" if is_test_report else f"ai_slop_audit_{next_idx - 1}.html"
        report_history_html = f"""
            <div class="sidebar-section">
                <div class="section-title">Report History</div>
                <a href="./{prev_file_name}" class="prev-report-link">
                    <svg viewBox="0 0 24 24" width="14" height="14" stroke="currentColor" stroke-width="2" fill="none"><path d="M15 18l-6-6 6-6"></path></svg>
                    View Report #{next_idx - 1} (Score: {previous_score}/10)
                </a>
            </div>
        """

    # Build "Changes Since Report #N-1" grid section
    what_changed_html = ""
    if previous_findings and next_idx > 1:
        resolved_items = ""
        for pf in resolved_findings:
            resolved_items += f"""
            <div class="change-item">
                <span class="change-icon check">✓</span>
                <span><strong>{escape_html(pf['title'])}</strong> — Resolved since Report #{next_idx - 1}</span>
            </div>"""
        if not resolved_items:
            resolved_items = '<div class="change-item"><span style="color:var(--text-light); font-size:13px;">No findings resolved in this pass.</span></div>'

        open_items = ""
        still_open_count = 0
        new_count = 0
        for f in findings:
            # Check if this current finding is similar to a previous finding
            is_open = False
            for pf in previous_findings:
                if is_similar_finding(pf, f):
                    is_open = True
                    break
            
            if is_open:
                still_open_count += 1
                open_items += f"""
                <div class="change-item">
                    <span class="change-icon warning">⚠</span>
                    <span><strong>{escape_html(f['title'])}</strong> <span style="font-size: 11px; color: var(--text-light); font-weight: 500;">(Still Open)</span> — <code style="font-size:11px;">{escape_html(f['location'])}</code></span>
                </div>"""
            else:
                new_count += 1
                open_items += f"""
                <div class="change-item">
                    <span class="change-icon plus">+</span>
                    <span><strong>{escape_html(f['title'])}</strong> <span style="font-size: 11px; color: var(--primary); font-weight: 600;">(New)</span> — <code style="font-size:11px;">{escape_html(f['location'])}</code></span>
                </div>"""
                
        if not open_items:
            open_items = '<div class="change-item"><span style="color:#10b981; font-size:13px; font-weight:600;">All previous findings resolved!</span></div>'

        what_changed_html = f"""
        <div class="section-card" id="changes">
            <h2>
                <svg viewBox="0 0 24 24" width="22" height="22" stroke="currentColor" stroke-width="2.5" fill="none" stroke-linecap="round" stroke-linejoin="round" style="color: #10b981;"><polyline points="22 4 12 14.01 9 11.01"></polyline><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path></svg>
                <span>Changes Since Report #{next_idx - 1}</span>
            </h2>
            <p>The following issues were resolved during the remediation pass, and open findings are tracked below.</p>
            <div class="changes-grid">
                <div class="change-box resolved">
                    <h4>✓ Resolved ({len(resolved_findings)} of {len(previous_findings)})</h4>
                    {resolved_items}
                </div>
                <div class="change-box new">
                    <h4>⚠ Still Open / New ({still_open_count} open, {new_count} new)</h4>
                    {open_items}
                </div>
            </div>
        </div>"""

    # Strip any pre-existing overall severity lines, score lines, code blocks, tables, and trailing dividers from overview_body
    clean_overview_body = re.sub(r'^(?:#+\s*|\d+\.\s*|Executive Overview:?\s*).*$', '', overview_body, flags=re.MULTILINE | re.IGNORECASE)
    clean_overview_body = re.sub(r'^\s*###?\s*\d+(?:\.\d+)*\s*.*$', '', clean_overview_body, flags=re.MULTILINE)
    clean_overview_body = re.sub(r'(?:\*\*|\b)Overall\s+(?:slop\s+|performance\s+|security\s+|test\s+quality\s+)?severity(?:\*\*|\b).*?(?:$|\n)', '', clean_overview_body, flags=re.IGNORECASE)
    clean_overview_body = re.sub(r'(?:\*\*|\b)Overall\s+(?:slop\s+|performance\s+|security\s+|test\s+quality\s+)?score(?:\*\*|\b).*?(?:$|\n)', '', clean_overview_body, flags=re.IGNORECASE)
    clean_overview_body = re.sub(r'(?:\*\*|\b)Overall\s+(?:slop\s+|performance\s+|security\s+|test\s+quality\s+)?rating(?:\*\*|\b).*?(?:$|\n)', '', clean_overview_body, flags=re.IGNORECASE)
    clean_overview_body = re.sub(r'```[\s\S]*?```', '', clean_overview_body)
    clean_overview_body = re.sub(r'\|[\s\S]*?\|', '', clean_overview_body)
    clean_overview_body = clean_overview_body.strip()

    # Filter out dividers, justification blocks, tables, and incomplete lines ending with colons or transition phrases
    overview_paras = [p.strip() for p in clean_overview_body.split('\n\n') if p.strip()]
    valid_paras = []
    for p in overview_paras:
        if p.startswith('---') or p.lower().startswith('justification:') or p.startswith('|'):
            continue
        p_lines = [l for l in p.splitlines() if l.strip() and not l.strip().endswith(':') and not any(l.strip().lower().startswith(w) for w in ["two paths", "hot path", "diagram", "below:"])]
        if p_lines:
            valid_paras.append("\n".join(p_lines))

    if valid_paras:
        intro_text = valid_paras[0]
    else:
        if "performance" in report_title.lower():
            intro_text = f"Performance audit analysis of {dir_name} evaluating runtime responsiveness, CPU/event-loop bottlenecks, query efficiency, memory utilization, and algorithmic performance across core workflows."
        elif "security" in report_title.lower():
            intro_text = f"Security audit analysis of {dir_name} evaluating code security posture, OWASP Top 10 vulnerabilities, credential guards, token handling, and sandbox safety."
        elif is_test_report:
            intro_text = f"Test quality audit analysis of {dir_name} evaluating test suite depth, mock configurations, edge case handling, and test harness execution safety."
        else:
            intro_text = f"Forensic code audit of {dir_name} evaluating codebase health, architectural consistency, maintainability, and code quality patterns."

    overview_html = markdown_to_html(intro_text)
    overview_html = clean_trailing_separators(overview_html)

    fact_sentence = f"<strong>Overall Severity: {verdict}</strong> — The audit identified <strong>{high_count} High</strong>, <strong>{medium_count} Medium</strong>, and <strong>{low_count} Low</strong> severity findings across the codebase."
    overview_html = overview_html.strip() + f"<p>{fact_sentence}</p>"

    # Generate Findings HTML list
    findings_html = ""
    for f in findings:
        severity = map_severity(f['likelihood'])
        badge_class = f"badge-{severity.lower()}"
        card_class = f"card-severity-{severity.lower()}"
        
        code_text = f['code_snippet']
        code_text = extract_code_from_file_if_needed(f['location'], code_text, proj_dir)
        if not code_text or not code_text.strip():
            code_text = f"// Location: {f['location']}\n// Finding: {f['title']}"
            
        filename = os.path.basename(f['location'].split(':')[0]) if f['location'] else "evidence.ts"
        code_lang = f.get('code_lang') or detect_code_language(filename)
        code_html = f"""
        <div class="code-window">
            <div class="code-header">
                <div class="code-dots">
                    <span class="dot red"></span>
                    <span class="dot yellow"></span>
                    <span class="dot green"></span>
                </div>
                <div class="code-tab">
                    <svg class="file-icon" viewBox="0 0 24 24" width="12" height="12" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline></svg>
                    <span>{escape_html(filename)}</span>
                </div>
            </div>
            <div class="code-body">
                <pre><code class="language-{escape_html(code_lang)}">{escape_html(code_text)}</code></pre>
            </div>
        </div>
        """

        description_html = markdown_to_html(f['description'])
        reasoning_html = markdown_to_html(f['reasoning'])
        
        # Determine impact level
        impact = map_impact(f['risk'])
        
        # Add context and reason explaining the severity level
        severity_explanation = ""
        if impact == "High":
            severity_explanation = "<p><strong>Severity Classification (High Impact):</strong> This pattern indicates a severe issue that threatens codebase execution, breaks TypeScript/build compilation, or introduces critical logic failures. Immediate remediation is recommended.</p>"
        elif impact == "Medium":
            severity_explanation = "<p><strong>Severity Classification (Medium Impact):</strong> This pattern represents moderate code duplication, unnecessary abstraction, or incomplete configurations. It hinders maintainability and introduces code drift risks, but does not crash the system immediately.</p>"
        else:
            severity_explanation = "<p><strong>Severity Classification (Low Impact):</strong> This pattern represents minor code hygiene issues, unused imports, dead variables, or missing tests. It has low runtime risk but should be cleaned up during general maintenance.</p>"
            
        risk_raw = f['risk']
        clean_risk_raw = risk_raw.strip().lower() if risk_raw else ""
        clean_risk_raw_base = re.sub(r'\s*\(.*?\)', '', clean_risk_raw).strip()
        
        if clean_risk_raw_base in ["high", "medium", "low", "critical", "unknown", ""]:
            risk_html = severity_explanation
        else:
            risk_html = severity_explanation + markdown_to_html(risk_raw)

        findings_html += f"""
        <div class="finding-card {card_class} is-collapsed">
            <div class="finding-header" onclick="toggleFindingCard(this)">
                <div class="finding-title-group">
                    <span class="finding-id">{escape_html(f['id'])}</span>
                    <h3 class="finding-title">{escape_html(f['title'])}</h3>
                </div>
                <div class="finding-header-right">
                    <span class="badge {badge_class}">{escape_html(severity)}</span>
                    <svg class="chevron-icon" viewBox="0 0 24 24" width="18" height="18" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"></polyline></svg>
                </div>
            </div>
            
            <div class="finding-body">
                <div class="finding-meta">
                    <div class="meta-item">
                        <span class="meta-label">Location</span>
                        <span class="meta-value font-mono">
                            {escape_html(f['location'])}
                        </span>
                    </div>
                </div>
                
                {code_html}
                
                <div class="finding-details-grid">
                    <div class="details-box reasoning">
                        <h4>Audit Finding Details</h4>
                        <div>{reasoning_html}</div>
                    </div>
                    <div class="details-box risk">
                        <h4>Impact & Risk</h4>
                        <div>{risk_html}</div>
                    </div>
                </div>
            </div>
        </div>
        """
    # Generate Scorecard rows directly from sorted findings
    scorecard_rows = ""
    for f in findings:
        fid = f['id']
        f_title = f['title']
        loc = f['location']
        slop_state = map_severity(f['likelihood'])
        impact = map_impact(f['risk'])
        
        badge_class = f"badge-{slop_state.lower()}"
        impact_class = f"impact-{impact.lower()}"
        
        scorecard_rows += f"""
        <tr>
            <td class="font-mono text-center font-bold" style="width: 55px; white-space: nowrap; vertical-align: middle;">{escape_html(fid)}</td>
            <td style="width: 42%; vertical-align: middle;"><strong>{escape_html(f_title)}</strong></td>
            <td class="font-mono text-muted" style="width: 28%; font-size: 11.5px; word-break: break-all; vertical-align: middle;">{escape_html(loc)}</td>
            <td style="text-align: center; width: 15%; white-space: nowrap; vertical-align: middle;"><span class="badge {badge_class}">{escape_html(slop_state)}</span></td>
            <td style="text-align: center; width: 15%; white-space: nowrap; vertical-align: middle;"><span class="impact-indicator {impact_class}">{escape_html(impact)}</span></td>
        </tr>
        """

    # Format metadata section
    meta_rows_html = ""
    for k, v in metadata.items():
        if k.lower() in ("method", "repository", "scope"):
            continue
        val_clean = v.replace("`", "")
        if k.lower() == "date":
            utc_now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
            meta_rows_html += f"""
        <div class="meta-row">
            <span class="meta-label">{escape_html(k)}</span>
            <span class="meta-value" id="audit-date-time" data-utc="{utc_now_iso}">{escape_html(val_clean)}</span>
        </div>
        """
        else:
            meta_rows_html += f"""
        <div class="meta-row">
            <span class="meta-label">{escape_html(k)}</span>
            <span class="meta-value">{escape_html(val_clean)}</span>
        </div>
        """

    slop_state_header = "Severity"
    if "performance" in report_title.lower():
        sub_heading_text = "Copy and run this engineered prompt with an AI coding assistant (e.g. Antigravity, Claude, ChatGPT) to automatically resolve all performance bottlenecks, optimize runtime loops, and improve your codebase score:"
    elif "security" in report_title.lower():
        sub_heading_text = "Copy and run this engineered prompt with an AI coding assistant (e.g. Antigravity, Claude, ChatGPT) to automatically remediate all security vulnerabilities, fix access controls, and improve your codebase score:"
    elif is_test_report:
        sub_heading_text = "Copy and run this engineered prompt with an AI coding assistant (e.g. Antigravity, Claude, ChatGPT) to automatically resolve all testing gaps, write missing test cases, and improve your codebase score:"
    else:
        sub_heading_text = "Copy and run this engineered prompt with an AI coding assistant (e.g. Antigravity, Claude, ChatGPT) to automatically resolve all codebase issues, clean up code debt, and improve your codebase score:"

    # Build remediation section HTML (synthesize if not provided)
    if not remediation_body and findings:
        repo_name = metadata.get("Repository", dir_name)
        if is_test_report:
            rem_lines = [
                f"Act as a Principal Software Engineer. Write missing automated test cases, mock required native modules, and resolve testing gaps in {repo_name} to improve code coverage and test quality:\n"
            ]
        else:
            rem_lines = [
                f"Act as a Principal Software Engineer. Clean up low-signal or duplicate code, remove speculative stubs, and resolve codebase issues in {repo_name} to improve maintainability and clean up AI-generated slop:\n"
            ]
        for idx, f in enumerate(findings, 1):
            # Strip any prefix like F1 or Finding 1 from the title
            clean_title = re.sub(r'^\[(?:Finding|F)?\s*\d+\]\s*', '', f['title'], flags=re.IGNORECASE).strip()
            clean_title = re.sub(r'^F?\d+[\.\s\-:]+\s*', '', clean_title, flags=re.IGNORECASE).strip()
            rem_lines.append(f"{idx}. {clean_title}:")
            rem_lines.append(f"   - Target Location: {f['location']}")
            if f.get('reasoning'):
                rem_lines.append(f"   - Action: {f['reasoning']}")
            elif f.get('impact'):
                rem_lines.append(f"   - Action: {f['impact']}")
            else:
                rem_lines.append(f"   - Action: Address and resolve this issue.")
            rem_lines.append("")
        rem_lines.append("Run all relevant test suites and build checks to ensure no regressions remain.")
        remediation_body = "\n".join(rem_lines)

    remediation_html = ""
    if remediation_body:
        # Strip any leading headers/labels from the body
        clean_rem = re.sub(r'^#+\s*(?:AI\s+)?Remediation\s*(?:Prompt)?\s*\n?', '', remediation_body, flags=re.IGNORECASE).strip()
        
        # Clean any findings / F1 / F2 / finding words from the prompt body
        clean_rem = re.sub(r'\[F\d+\]', '', clean_rem)
        clean_rem = re.sub(r'\bF\d+[\.\s\-:]+', '', clean_rem)
        clean_rem = re.sub(r'\bFinding\s+\d+[\.\s\-:]+', '', clean_rem, flags=re.IGNORECASE)
        
        if is_test_report:
            # Map general slop prompt template lines to test-case phrasing
            clean_rem = re.sub(r'clean up AI slop and resolve all audit findings', 'resolve all testing gaps and write missing tests', clean_rem, flags=re.IGNORECASE)
            clean_rem = re.sub(r'resolve all audit findings', 'resolve all testing gaps and write missing tests', clean_rem, flags=re.IGNORECASE)
            clean_rem = re.sub(r'\bfindings\b', 'testing gaps', clean_rem, flags=re.IGNORECASE)
            clean_rem = re.sub(r'\bfinding\b', 'testing gap', clean_rem, flags=re.IGNORECASE)
        else:
            clean_rem = re.sub(r'\bfindings\b', 'issues', clean_rem, flags=re.IGNORECASE)
            clean_rem = re.sub(r'\bfinding\b', 'issue', clean_rem, flags=re.IGNORECASE)

        # Unwrap artificial hard line breaks within paragraphs/bullets so text fills 100% width
        rem_lines = clean_rem.splitlines()
        unwrapped_lines = []
        curr_block = []
        for line in rem_lines:
            stripped = line.strip()
            if not stripped:
                if curr_block:
                    unwrapped_lines.append(" ".join(curr_block))
                    curr_block = []
                unwrapped_lines.append("")
            elif re.match(r'^(?:[-*+]\s+|\d+[\.\)]\s+|###?\s+|\*\*\*|\w+:)', stripped):
                if curr_block:
                    unwrapped_lines.append(" ".join(curr_block))
                    curr_block = []
                curr_block.append(line.rstrip())
            else:
                if curr_block:
                    curr_block.append(stripped)
                else:
                    curr_block.append(line.rstrip())
        if curr_block:
            unwrapped_lines.append(" ".join(curr_block))
        clean_rem = "\n".join(unwrapped_lines)

        remediation_html = f"""
        <div class="section-card" id="remediation" style="margin-top: 40px;">
            <div style="display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-bottom: 16px; flex-wrap: wrap;">
                <h2 style="margin-bottom:0;">
                    <svg viewBox="0 0 24 24" width="22" height="22" stroke="currentColor" stroke-width="2.5" fill="none" stroke-linecap="round" stroke-linejoin="round" style="color: var(--primary);"><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"></path></svg>
                    <span>AI Remediation Prompt</span>
                </h2>
                <button class="sc-pill" style="background: var(--primary); color: #ffffff; border-color: var(--primary); display: flex; align-items: center; gap: 6px; padding: 6px 14px; font-weight: 600;" onclick="copyRemediationPrompt()">
                    <svg viewBox="0 0 24 24" width="14" height="14" stroke="currentColor" stroke-width="2" fill="none"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>
                    Copy Prompt
                </button>
            </div>
            <p style="margin-bottom: 16px;">{sub_heading_text}</p>
            <div class="code-window" style="background: #ffffff; border-color: var(--border-color); box-shadow: var(--shadow-sm); margin: 14px 0; border-radius: 8px; overflow: hidden; width: 100%;">
                <div class="code-header" style="background: var(--highlight-bg); border-color: var(--border-color); display: flex; align-items: center; padding: 8px 14px; border-bottom: 1px solid var(--border-color); user-select: none;">
                    <div class="code-dots" style="display: flex; gap: 6px; margin-right: 16px;">
                        <span class="dot red" style="background: #cbd5e1; width: 8px; height: 8px; border-radius: 50%; display: inline-block;"></span>
                        <span class="dot yellow" style="background: #cbd5e1; width: 8px; height: 8px; border-radius: 50%; display: inline-block;"></span>
                        <span class="dot green" style="background: #cbd5e1; width: 8px; height: 8px; border-radius: 50%; display: inline-block;"></span>
                    </div>
                    <div class="code-tab" style="background: #ffffff; color: var(--text-main); border-color: var(--border-color); font-family: 'Fira Code', monospace; font-size: 11px; padding: 2px 8px; border-radius: 4px; display: flex; align-items: center; gap: 6px; font-weight: 500;">
                        <svg class="file-icon" viewBox="0 0 24 24" width="12" height="12" stroke="currentColor" stroke-width="2" fill="none"><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"></path></svg>
                        AI_Remediation_Prompt.txt
                    </div>
                </div>
                <div class="code-body" style="background: #ffffff; max-height: 280px; overflow-y: auto; padding: 16px; overflow-x: auto; border: 1px solid var(--border-color); border-top: none; border-radius: 0 0 8px 8px; width: 100%;">
                    <pre style="margin: 0; width: 100%;"><code id="remediation-prompt-text" style="color: var(--text-main); font-size: 13px; line-height: 1.6; font-family: 'Fira Code', monospace; white-space: pre-wrap; word-break: break-word; display: block; width: 100%;">{escape_html(clean_rem)}</code></pre>
                </div>
            </div>`
        </div>"""

    # Build Premium Light Theme HTML Content
    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{escape_html(display_title)}</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=Fira+Code:wght@400;500&display=swap" rel="stylesheet">
    <style>
        :root {{
            --bg: #f8fafc;
            --card-bg: #ffffff;
            --text-main: #0f172a;
            --text-muted: #475569;
            --text-light: #64748b;
            --border-color: #e2e8f0;
            --primary: #4f46e5;
            --primary-hover: #4338ca;
            --code-bg: #0f172a;
            --sidebar-bg: #f1f5f9;
            --sidebar-border: #e2e8f0;
            --highlight-bg: #e2e8f0;
            --shadow-sm: 0 1px 2px 0 rgba(0, 0, 0, 0.05);
            --shadow-md: 0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -2px rgba(0, 0, 0, 0.1);
            --shadow-lg: 0 10px 15px -3px rgba(0, 0, 0, 0.1), 0 4px 6px -4px rgba(0, 0, 0, 0.1);
            --toast-bg: #0f172a;
            --toast-color: #ffffff;
        }}

        * {{
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }}

        body {{
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background: var(--bg);
            color: var(--text-main);
            line-height: 1.6;
            -webkit-font-smoothing: antialiased;
        }}

        /* Dashboard Layout */
        .dashboard-layout {{
            display: flex;
            min-height: 100vh;
        }}

        .sidebar {{
            width: 300px;
            background: var(--sidebar-bg);
            border-right: 1px solid var(--sidebar-border);
            padding: 32px 24px;
            position: sticky;
            top: 0;
            height: 100vh;
            display: flex;
            flex-direction: column;
            gap: 28px;
            overflow-y: auto;
            flex-shrink: 0;
        }}

        .main-content {{
            flex: 1;
            padding: 40px 24px;
            overflow-y: auto;
            display: flex;
            flex-direction: column;
            align-items: center;
        }}

        .container {{
            width: 100%;
            max-width: 1000px;
        }}

        /* Sidebar components */
        .sidebar-header .brand {{
            font-size: 18px;
            font-weight: 800;
            letter-spacing: -0.02em;
            color: var(--primary);
            display: flex;
            align-items: center;
            gap: 8px;
        }}

        .sidebar-section {{
            display: flex;
            flex-direction: column;
            gap: 12px;
        }}

        .sidebar-section .section-title {{
            font-size: 10px;
            font-weight: 800;
            text-transform: uppercase;
            letter-spacing: 0.1em;
            color: var(--text-light);
        }}

        .nav-links {{
            display: flex;
            flex-direction: column;
            gap: 4px;
        }}

        .nav-item {{
            display: flex;
            align-items: center;
            gap: 10px;
            padding: 8px 12px;
            border-radius: 8px;
            font-size: 13.5px;
            font-weight: 500;
            color: var(--text-muted);
            text-decoration: none;
            transition: all 0.2s;
        }}

        .nav-item:hover {{
            background: var(--highlight-bg);
            color: var(--text-main);
        }}

        .nav-item.active {{
            background: var(--highlight-bg);
            color: var(--text-main);
            font-weight: 600;
        }}

        .filter-controls {{
            display: flex;
            flex-direction: column;
            gap: 16px;
        }}

        #search-input {{
            width: 100%;
            padding: 10px 14px;
            border-radius: 8px;
            border: 1px solid var(--border-color);
            background: var(--card-bg);
            color: var(--text-main);
            font-size: 13.5px;
            outline: none;
            transition: border-color 0.2s, background-color 0.2s;
        }}

        #search-input:focus {{
            border-color: var(--primary);
        }}

        .filter-label {{
            font-size: 10px;
            font-weight: 800;
            text-transform: uppercase;
            color: var(--text-light);
            margin-bottom: 6px;
            display: block;
        }}

        .severity-pills {{
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 6px;
        }}

        .sev-pill {{
            background: var(--card-bg);
            border: 1px solid var(--border-color);
            color: var(--text-muted);
            font-size: 11.5px;
            font-weight: 600;
            padding: 6px 0;
            border-radius: 6px;
            cursor: pointer;
            text-align: center;
            transition: all 0.2s;
        }}

        .sev-pill:hover {{
            background: var(--highlight-bg);
        }}

        .sev-pill.active {{
            color: #ffffff !important;
        }}

        .sev-pill.active[data-severity="all"] {{ background: var(--primary); border-color: var(--primary); }}
        .sev-pill.active[data-severity="high"] {{ background: #ef4444; border-color: #ef4444; }}
        .sev-pill.active[data-severity="medium"] {{ background: #f97316; border-color: #f97316; }}
        .sev-pill.active[data-severity="low"] {{ background: #10b981; border-color: #10b981; }}

        .filter-stats {{
            font-size: 12px;
            color: var(--text-light);
            font-weight: 500;
        }}

        .sidebar-btn {{
            width: 100%;
            padding: 8px;
            border-radius: 8px;
            border: 1px solid var(--border-color);
            background: var(--card-bg);
            color: var(--text-muted);
            font-size: 12.5px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s;
        }}

        .sidebar-btn:hover {{
            background: var(--highlight-bg);
            color: var(--text-main);
        }}

        /* Top Header layout */
        .top-layout {{
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            gap: 32px;
            margin-bottom: 32px;
            border-bottom: 1px solid var(--border-color);
            padding-bottom: 32px;
        }}

        .top-layout header {{
            flex: 1;
        }}

        .pre-header {{
            font-size: 11px;
            font-weight: 800;
            text-transform: uppercase;
            letter-spacing: 0.15em;
            color: var(--primary);
            margin-bottom: 12px;
        }}

        h1 {{
            font-size: 32px;
            font-weight: 800;
            letter-spacing: -0.03em;
            line-height: 1.2;
            margin-bottom: 16px;
            color: var(--text-main);
        }}

        .meta-section {{
            background: var(--card-bg);
            border: 1px solid var(--border-color);
            border-radius: 8px;
            padding: 14px 18px;
            margin-top: 16px;
            display: grid;
            grid-template-columns: 1fr;
            gap: 12px;
            box-shadow: var(--shadow-sm);
        }}

        @media (min-width: 600px) {{
            .meta-section {{
                grid-template-columns: 1fr 1fr;
            }}
        }}

        .meta-row {{
            display: flex;
            flex-direction: column;
            gap: 2px;
        }}

        .meta-label {{
            font-size: 9px;
            font-weight: 800;
            text-transform: uppercase;
            letter-spacing: 0.1em;
            color: var(--text-light);
        }}

        .meta-value {{
            font-size: 13px;
            font-weight: 500;
            color: var(--text-main);
            line-height: 1.4;
        }}

        /* Scorecard styling */
        .score-card-wrapper {{
            display: flex;
            justify-content: center;
            align-items: center;
        }}

        .score-card {{
            width: 180px;
            padding: 20px 16px;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            text-align: center;
            border: 1px solid var(--border-color);
            background: var(--card-bg);
            border-radius: 12px;
            box-shadow: var(--shadow-md);
        }}

        .score-title {{
            font-size: 9px;
            font-weight: 800;
            text-transform: uppercase;
            letter-spacing: 0.1em;
            color: var(--text-light);
            margin-bottom: 6px;
            display: flex;
            align-items: center;
            justify-content: center;
        }}

        .info-tooltip {{
            position: relative;
            display: inline-block;
            margin-left: 6px;
            vertical-align: middle;
        }}

        .info-tooltip .tooltip-text {{
            visibility: hidden;
            width: 220px;
            background-color: #1e293b;
            color: #fff;
            text-align: left;
            border-radius: 6px;
            padding: 10px 12px;
            position: absolute;
            z-index: 9999;
            top: 130%;
            right: 0;
            opacity: 0;
            transition: opacity 0.2s;
            font-size: 11px;
            line-height: 1.4;
            font-weight: 400;
            box-shadow: var(--shadow-lg);
            pointer-events: none;
        }}

        .info-tooltip .tooltip-text::after {{
            content: "";
            position: absolute;
            bottom: 100%;
            right: 12px;
            border-width: 6px;
            border-style: solid;
            border-color: transparent transparent #1e293b transparent;
        }}

        .info-tooltip:hover .tooltip-text {{
            visibility: visible;
            opacity: 1;
        }}

        .score-value {{
            font-size: 48px;
            font-weight: 800;
            line-height: 1;
            margin-top: 4px;
            font-feature-settings: "tnum";
        }}

        .score-scale {{
            font-size: 16px;
            font-weight: 500;
            color: var(--text-light);
        }}

        .earlier-score {{
            font-size: 12px;
            font-weight: 600;
            color: var(--text-light);
            margin-top: 4px;
            text-transform: lowercase;
        }}

        /* Score Explanation Note */
        .score-explanation-note {{
            font-size: 13.5px;
            color: var(--text-muted);
            margin-bottom: 24px;
            line-height: 1.6;
        }}

        .section-card {{
            background: var(--card-bg);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            padding: 24px;
            box-shadow: var(--shadow-sm);
            margin-bottom: 24px;
            scroll-margin-top: 24px;
        }}

        h2 {{
            font-size: 20px;
            font-weight: 800;
            letter-spacing: -0.02em;
            margin-bottom: 18px;
            display: flex;
            align-items: center;
            gap: 10px;
            color: var(--text-main);
        }}

        p {{
            font-size: 14.5px;
            color: var(--text-muted);
            margin-bottom: 14px;
            line-height: 1.6;
        }}

        p:last-child {{
            margin-bottom: 0;
        }}

        .findings-section-header {{
            margin-top: 40px;
            margin-bottom: 20px;
            font-size: 22px;
            font-weight: 800;
            color: var(--text-main);
            letter-spacing: -0.02em;
            scroll-margin-top: 24px;
        }}

        /* Collapsible Finding Card styling */
        .finding-card {{
            background: var(--card-bg);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            padding: 0;
            margin-bottom: 16px;
            box-shadow: var(--shadow-sm);
            transition: all 0.2s ease;
            overflow: hidden;
            scroll-margin-top: 24px;
        }}

        .finding-card:hover {{
            box-shadow: var(--shadow-md);
            border-color: #cbd5e1;
        }}

        .finding-header {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 16px;
            padding: 16px 20px;
            cursor: pointer;
            user-select: none;
            background: var(--card-bg);
            transition: background-color 0.2s;
        }}

        .finding-header:hover {{
            background: var(--highlight-bg);
        }}

        .finding-header-right {{
            display: flex;
            align-items: center;
            gap: 12px;
        }}

        .chevron-icon {{
            color: var(--text-light);
            transition: transform 0.2s ease;
        }}

        .finding-card.is-collapsed .chevron-icon {{
            transform: rotate(-90deg);
        }}

        .finding-body {{
            padding: 16px 20px 20px 20px;
            border-top: 1px solid var(--border-color);
        }}

        .finding-card.is-collapsed .finding-body {{
            display: none;
        }}

        .finding-title-group {{
            display: flex;
            align-items: center;
            gap: 10px;
            flex: 1;
            min-width: 250px;
        }}

        .finding-id {{
            background: var(--highlight-bg);
            color: var(--text-muted);
            font-weight: 800;
            padding: 4px 8px;
            border-radius: 6px;
            font-size: 11px;
            letter-spacing: 0.05em;
            font-family: 'Fira Code', monospace;
        }}

        .finding-title {{
            font-size: 16px;
            font-weight: 700;
            margin: 0 !important;
            color: var(--text-main);
            letter-spacing: -0.01em;
            border-bottom: none !important;
            padding-bottom: 0 !important;
            text-decoration: none !important;
        }}

        .badge {{
            font-size: 10px;
            font-weight: 700;
            padding: 3px 10px;
            border-radius: 9999px;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            border: 1px solid transparent;
            display: inline-block;
            white-space: nowrap;
        }}

        .badge-high {{ background: #fef2f2; color: #ef4444; border-color: #fca5a5; }}
        .badge-medium {{ background: #fff7ed; color: #f97316; border-color: #fed7aa; }}
        .badge-low {{ background: #ecfdf5; color: #10b981; border-color: #a7f3d0; }}

        .finding-meta {{
            margin-bottom: 14px;
            border-bottom: 1px solid var(--border-color);
            padding-bottom: 10px;
        }}

        .meta-item {{
            display: flex;
            align-items: center;
            gap: 8px;
        }}

        .meta-label {{
            font-size: 9px;
            font-weight: 800;
            text-transform: uppercase;
            color: var(--text-light);
            letter-spacing: 0.05em;
        }}

        .meta-value {{
            font-size: 12px;
            color: var(--text-muted);
            display: flex;
            align-items: center;
            gap: 6px;
            flex-wrap: wrap;
        }}

        /* Light Theme Code Block Window */
        .code-window {{
            background: #f8fafc;
            border-radius: 8px;
            border: 1px solid #e2e8f0;
            overflow: hidden;
            margin: 14px 0;
            box-shadow: var(--shadow-sm);
        }}

        .code-header {{
            display: flex;
            align-items: center;
            background: #f1f5f9;
            padding: 8px 14px;
            border-bottom: 1px solid #e2e8f0;
            user-select: none;
        }}

        .code-dots {{
            display: flex;
            gap: 6px;
            margin-right: 16px;
        }}

        .code-dots .dot {{
            width: 8px;
            height: 8px;
            border-radius: 50%;
            display: inline-block;
        }}

        .code-dots .dot.red {{ background: #cbd5e1; }}
        .code-dots .dot.yellow {{ background: #cbd5e1; }}
        .code-dots .dot.green {{ background: #cbd5e1; }}

        .code-tab {{
            font-family: 'Fira Code', monospace;
            font-size: 11px;
            color: #334155;
            background: #ffffff;
            border: 1px solid #cbd5e1;
            padding: 2px 8px;
            border-radius: 4px;
            display: flex;
            align-items: center;
            gap: 6px;
            font-weight: 500;
        }}

        .file-icon {{
            color: #64748b;
        }}

        .code-body {{
            padding: 16px;
            overflow-x: auto;
            max-height: 350px;
            background: #f8fafc;
        }}

        .code-body pre {{
            margin: 0;
        }}

        .code-body code {{
            font-family: 'Fira Code', monospace;
            font-size: 12.5px;
            line-height: 1.5;
            color: #0f172a;
        }}

        /* Finding Details Grid */
        .finding-details-grid {{
            display: grid;
            grid-template-columns: 1fr;
            gap: 16px;
            margin-top: 18px;
        }}

        @media (min-width: 768px) {{
            .finding-details-grid {{
                grid-template-columns: 1fr 1fr;
            }}
        }}

        .details-box {{
            padding: 14px 18px;
            border-radius: 8px;
            border: 1px solid var(--border-color);
        }}

        .details-box.reasoning {{
            background: #f8fafc;
            border-color: #e2e8f0;
        }}

        .details-box.risk {{
            background: #f8fafc;
            border-color: #e2e8f0;
        }}

        .details-box h4 {{
            margin-top: 0;
            margin-bottom: 6px;
            font-size: 10px;
            font-weight: 800;
            text-transform: uppercase;
            letter-spacing: 0.08em;
            color: var(--text-main);
        }}

        .details-box div {{
            font-size: 13px;
            line-height: 1.6;
            color: var(--text-muted);
        }}

        .section-card h3 {{
            font-size: 15px;
            font-weight: 800;
            margin-top: 24px;
            margin-bottom: 10px;
            color: var(--text-main);
            border-bottom: 1px solid var(--border-color);
            padding-bottom: 6px;
            display: flex;
            align-items: center;
            gap: 8px;
        }}
        
        .section-card h3:first-of-type {{
            margin-top: 10px;
        }}

        .section-card ul, .section-card ol {{
            margin-left: 20px;
            margin-bottom: 14px;
        }}

        .section-card li {{
            font-size: 13.5px;
            color: var(--text-muted);
            margin-bottom: 6px;
            line-height: 1.6;
        }}

        ul, ol {{
            padding-left: 20px;
            margin-bottom: 12px;
        }}

        li {{
            margin-bottom: 6px;
            font-size: 14px;
            color: var(--text-muted);
        }}

        .inline-code {{
            font-family: 'Fira Code', monospace;
            font-size: 12px;
            background: var(--highlight-bg);
            padding: 2px 4px;
            border-radius: 4px;
            color: var(--primary);
        }}

        .bottom-sections {{
            display: grid;
            grid-template-columns: 1fr;
            gap: 24px;
            margin-top: 40px;
            scroll-margin-top: 24px;
        }}

        @media (min-width: 768px) {{
            .bottom-sections {{
                grid-template-columns: 1fr 1fr;
            }}
        }}

        .scorecard-card {{
            margin-top: 40px;
            overflow-x: auto;
            scroll-margin-top: 24px;
        }}

        /* Table styles */
        .table-container {{
            width: 100%;
            overflow-x: auto;
            margin: 12px 0;
            border: 1px solid var(--border-color);
            border-radius: 8px;
            box-shadow: var(--shadow-sm);
        }}

        table {{
            width: 100%;
            border-collapse: collapse;
        }}

        th, td {{
            text-align: left;
            padding: 12px 16px;
            border-bottom: 1px solid var(--border-color);
            font-size: 13px;
            line-height: 1.5;
            word-break: break-word;
            overflow-wrap: anywhere;
        }}

        th {{
            font-weight: 700;
            text-transform: uppercase;
            font-size: 10px;
            letter-spacing: 0.1em;
            color: var(--text-light);
            background: var(--sidebar-bg);
            border-bottom: 2px solid var(--border-color);
        }}

        tr:last-child td {{
            border-bottom: none;
        }}

        tr:hover td {{
            background: var(--highlight-bg);
        }}

        td strong {{
            color: var(--text-main);
        }}

        .impact-indicator {{
            font-size: 11px;
            font-weight: 600;
            padding: 2px 6px;
            border-radius: 4px;
            display: inline-block;
            white-space: nowrap;
        }}
        .impact-high {{ background: #fef2f2; color: #ef4444; }}
        .impact-medium {{ background: #fff7ed; color: #f97316; }}
        .impact-low {{ background: #ecfdf5; color: #10b981; }}

        .scorecard-header-bar {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 16px;
            margin-bottom: 14px;
            flex-wrap: wrap;
        }}

        .scorecard-header-bar h2 {{
            margin-bottom: 0;
        }}

        .scorecard-filter-dropdown-wrap {{
            display: flex;
            align-items: center;
        }}

        .sc-filter-select {{
            background: #ffffff;
            color: var(--text-main);
            border: 1px solid var(--border-color);
            border-radius: 8px;
            padding: 6px 12px;
            font-size: 13px;
            font-weight: 600;
            cursor: pointer;
            outline: none;
            box-shadow: var(--shadow-sm);
            transition: all 0.2s ease;
        }}

        .sc-filter-select:hover, .sc-filter-select:focus {{
            border-color: var(--primary);
            box-shadow: 0 0 0 2px rgba(99, 102, 241, 0.15);
        }}

        .sc-pill {{
            background: var(--bg);
            border: 1px solid var(--border-color);
            color: var(--text-muted);
            font-size: 11.5px;
            font-weight: 600;
            padding: 4px 10px;
            border-radius: 6px;
            cursor: pointer;
            transition: all 0.2s;
        }}

        .sc-pill:hover {{
            background: var(--highlight-bg);
        }}

        .sc-pill.active {{
            background: var(--primary);
            border-color: var(--primary);
            color: #ffffff;
        }}

        
        /* Previous report link in sidebar */
        .prev-report-link {{
            display: flex;
            align-items: center;
            gap: 8px;
            padding: 10px 12px;
            border-radius: 8px;
            background: #eff6ff;
            border: 1px solid #bfdbfe;
            color: #1d4ed8;
            font-size: 12.5px;
            font-weight: 600;
            text-decoration: none;
            transition: all 0.2s;
        }}

        .prev-report-link:hover {{ background: #dbeafe; }}

        /* Scorecards side-by-side row */
        .score-cards-row {{
            display: flex;
            align-items: center;
            gap: 12px;
        }}

        .score-card.prev {{
            opacity: 0.6;
            transform: scale(0.92);
        }}

        .score-card.current {{
            border-color: #10b981;
            box-shadow: 0 0 0 3px rgba(16, 185, 129, 0.12), var(--shadow-md);
        }}

        .score-arrow {{
            color: #10b981;
            font-size: 20px;
            font-weight: 800;
        }}

        /* Changes from Previous Report Section */
        .changes-grid {{
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 16px;
            margin-top: 18px;
        }}

        @media (max-width: 640px) {{
            .changes-grid {{ grid-template-columns: 1fr; }}
        }}

        .change-box {{
            border-radius: 8px;
            border: 1px solid;
            padding: 16px 18px;
        }}

        .change-box.resolved {{
            background: #f0fdf4;
            border-color: #bbf7d0;
        }}

        .change-box.new {{
            background: #fff7ed;
            border-color: #fed7aa;
        }}

        .change-box h4 {{
            font-size: 11px;
            font-weight: 800;
            text-transform: uppercase;
            letter-spacing: 0.08em;
            margin-bottom: 12px;
        }}

        .change-box.resolved h4 {{ color: #15803d; }}
        .change-box.new h4 {{ color: #c2410c; }}

        .change-item {{
            display: flex;
            align-items: flex-start;
            gap: 8px;
            margin-bottom: 10px;
            font-size: 13px;
            color: var(--text-muted);
            line-height: 1.5;
        }}

        .change-item:last-child {{ margin-bottom: 0; }}

        .change-icon {{
            width: 16px;
            height: 16px;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            flex-shrink: 0;
            margin-top: 1px;
            font-size: 9px;
            font-weight: 800;
        }}

        .change-icon.check {{ background: #dcfce7; color: #15803d; }}
        .change-icon.plus {{ background: #fee2e2; color: #ef4444; }}
        .change-icon.warning {{ background: #ffedd5; color: #c2410c; }}

        /* Code block pre-wrap formatting without horizontal scroll */
        #remediation-prompt-text {{
            color: #e2e8f0;
            font-size: 12.5px;
            line-height: 1.6;
            white-space: pre-wrap;
            word-wrap: break-word;
            word-break: break-word;
            overflow-x: hidden;
        }}

        .code-body code {{
            font-family: 'Fira Code', monospace;
            font-size: 12.5px;
            line-height: 1.5;
            color: #0f172a;
            white-space: pre-wrap;
            word-break: break-word;
        }}

        /* Toast notifications */
        .toast-notification {{
            position: fixed;
            bottom: 24px;
            right: 24px;
            background: var(--toast-bg);
            color: var(--toast-color);
            padding: 12px 20px;
            border-radius: 8px;
            font-size: 13.5px;
            font-weight: 500;
            box-shadow: var(--shadow-lg);
            z-index: 1000;
            opacity: 0;
            transform: translateY(10px);
            transition: opacity 0.3s, transform 0.3s;
            pointer-events: none;
            display: flex;
            align-items: center;
            gap: 8px;
        }}

        .toast-notification.show {{
            opacity: 1;
            transform: translateY(0);
        }}

        /* What Changed diff section */
        .diff-summary-bar {{
            display: flex;
            align-items: center;
            gap: 12px;
            padding: 10px 14px;
            background: var(--sidebar-bg);
            border: 1px solid var(--border-color);
            border-radius: 8px;
            margin-bottom: 16px;
            flex-wrap: wrap;
        }}

        .diff-stat {{
            font-size: 13px;
            color: var(--text-muted);
        }}

        .diff-stat-divider {{
            color: var(--border-color);
            font-size: 16px;
        }}

        .diff-group {{
            display: flex;
            flex-direction: column;
            gap: 6px;
        }}

        .diff-group-label {{
            font-size: 10px;
            font-weight: 800;
            text-transform: uppercase;
            letter-spacing: 0.1em;
            color: var(--text-light);
            margin-bottom: 6px;
        }}

        .diff-item {{
            display: flex;
            align-items: center;
            gap: 10px;
            padding: 8px 12px;
            border-radius: 8px;
            border: 1px solid var(--border-color);
            background: var(--card-bg);
            flex-wrap: wrap;
        }}

        .diff-badge {{
            font-size: 10px;
            font-weight: 700;
            padding: 2px 8px;
            border-radius: 9999px;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            white-space: nowrap;
        }}

        .diff-badge-resolved {{
            background: #ecfdf5;
            color: #10b981;
            border: 1px solid #a7f3d0;
        }}

        .diff-badge-new {{
            background: #fef2f2;
            color: #ef4444;
            border: 1px solid #fca5a5;
        }}

        .diff-id {{
            font-family: 'Fira Code', monospace;
            font-size: 11px;
            font-weight: 800;
            background: var(--highlight-bg);
            color: var(--text-muted);
            padding: 2px 6px;
            border-radius: 4px;
            white-space: nowrap;
        }}

        .diff-title {{
            font-size: 13px;
            color: var(--text-main);
            font-weight: 500;
            flex: 1;
        }}

        .text-center {{ text-align: center; }}
        .text-muted {{ color: var(--text-muted); }}
        .font-mono {{ font-family: 'Fira Code', monospace; }}
        .font-bold {{ font-weight: 700; }}

        /* Responsive design */
        @media (max-width: 1024px) {{
            .dashboard-layout {{
                flex-direction: column;
            }}
            .sidebar {{
                width: 100%;
                height: auto;
                position: relative;
                padding: 24px;
                border-right: none;
                border-bottom: 1px solid var(--sidebar-border);
            }}
            .main-content {{
                padding: 24px 16px;
            }}
        }}
    </style>
</head>
<body>
    <div class="dashboard-layout">
        <aside class="sidebar">
            <div class="sidebar-header">
                <div class="brand">
                    <svg viewBox="0 0 24 24" width="20" height="20" stroke="currentColor" stroke-width="2.5" fill="none" style="color: var(--primary);"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"></path></svg>
                    Audit Report
                </div>
            </div>
            
            <div class="sidebar-section">
                <nav class="nav-links">
                    <a href="#overview" class="nav-item active">
                        <svg viewBox="0 0 24 24" width="15" height="15" stroke="currentColor" stroke-width="2" fill="none"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="16" x2="12" y2="12"></line><line x1="12" y1="8" x2="12.01" y2="8"></line></svg>
                        Executive Overview
                    </a>
                    {'<a href="#changes" class="nav-item"><svg viewBox="0 0 24 24" width="15" height="15" stroke="currentColor" stroke-width="2" fill="none"><polyline points="22 4 12 14.01 9 11.01"></polyline><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path></svg>Changes Since Report #' + str(next_idx - 1) + '</a>' if (what_changed_html and next_idx > 1) else ''}
                    <a href="#findings" class="nav-item">
                        <svg viewBox="0 0 24 24" width="15" height="15" stroke="currentColor" stroke-width="2" fill="none"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"></path><line x1="12" y1="9" x2="12" y2="13"></line><line x1="12" y1="17" x2="12.01" y2="17"></line></svg>
                        Detailed Findings
                    </a>
                    <a href="#observations" class="nav-item">
                        <svg viewBox="0 0 24 24" width="15" height="15" stroke="currentColor" stroke-width="2" fill="none"><path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"></path><path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"></path></svg>
                        Observations
                    </a>
                    <a href="#scorecard" class="nav-item">
                        <svg viewBox="0 0 24 24" width="15" height="15" stroke="currentColor" stroke-width="2" fill="none"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline><line x1="16" y1="13" x2="8" y2="13"></line><line x1="16" y1="17" x2="8" y2="17"></line><polyline points="10 9 9 9 8 9"></polyline></svg>
                        Summary Scorecard
                    </a>
                    {'<a href="#remediation" class="nav-item"><svg viewBox="0 0 24 24" width="15" height="15" stroke="currentColor" stroke-width="2" fill="none"><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"></path></svg>Remediation Prompt</a>' if remediation_html else ''}
                </nav>
            </div>
            
            {report_history_html}

            <div class="sidebar-section">
                <div class="section-title">Filters</div>
                <div class="filter-controls">
                    <div>
                        <input type="text" id="search-input" placeholder="Search findings...">
                    </div>
                </div>
            </div>
        </aside>
        
        <main class="main-content">
            <div class="container">
                <div class="top-layout">
                    <header>
                        <h1>{escape_html(display_title)}</h1>
                        <div class="meta-section">
                            {meta_rows_html}
                        </div>
                    </header>
                    
                    {score_card_wrapper_html}
                </div>

                {disclaimer_html}

                <div class="section-card" id="overview">
                    <h2>
                        <svg viewBox="0 0 24 24" width="22" height="22" stroke="currentColor" stroke-width="2.5" fill="none" stroke-linecap="round" stroke-linejoin="round" style="color: var(--primary);"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="16" x2="12" y2="12"></line><line x1="12" y1="8" x2="12.01" y2="8"></line></svg>
                        <span>Executive Overview</span>
                    </h2>
                    {overview_html}
                </div>

                {what_changed_html}

                <div id="findings" class="section-card" style="background: transparent; border: none; box-shadow: none; padding: 0; margin-bottom: 0;">
                    <h2 class="findings-section-header">Detailed Findings</h2>
                    <div class="findings-list">
                        {findings_html}
                    </div>
                </div>

                <div class="bottom-sections" id="observations">
                    <div class="section-card">
                        <h2>
                            <svg viewBox="0 0 24 24" width="22" height="22" stroke="currentColor" stroke-width="2.5" fill="none" stroke-linecap="round" stroke-linejoin="round" style="color: var(--primary);"><path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"></path><path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"></path></svg>
                            <span>Pattern Observations</span>
                        </h2>
                        {observations}
                    </div>
                    
                    <div class="section-card">
                        <h2>
                            <svg viewBox="0 0 24 24" width="22" height="22" stroke="currentColor" stroke-width="2.5" fill="none" stroke-linecap="round" stroke-linejoin="round" style="color: #10b981;"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path><polyline points="22 4 12 14.01 9 11.01"></polyline></svg>
                            <span>Stable & Intentional Areas</span>
                        </h2>
                        {stable_areas}
                    </div>
                </div>

                <div class="section-card scorecard-card" id="scorecard">
                    <div class="scorecard-header-bar">
                        <h2>
                            <svg viewBox="0 0 24 24" width="22" height="22" stroke="currentColor" stroke-width="2.5" fill="none" stroke-linecap="round" stroke-linejoin="round" style="color: var(--primary);"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline><line x1="16" y1="13" x2="8" y2="13"></line><line x1="16" y1="17" x2="8" y2="17"></line><polyline points="10 9 9 9 8 9"></polyline></svg>
                            <span>Summary Scorecard</span>
                        </h2>
                        <div class="scorecard-filter-dropdown-wrap">
                            <select class="sc-filter-select" id="scorecard-filter-select" onchange="filterScorecard(this.value)">
                                <option value="all">All Severities</option>
                                <option value="high">High Severity</option>
                                <option value="medium">Medium Severity</option>
                                <option value="low">Low Severity</option>
                            </select>
                        </div>
                    </div>
                    <div class="table-container">
                        <table style="table-layout: fixed; width: 100%;">
                            <thead>
                                <tr>
                                    <th class="text-center" style="width: 55px;">ID</th>
                                    <th style="width: 42%;">Finding</th>
                                    <th style="width: 28%;">Location</th>
                                    <th style="width: 15%; text-align: center;">{escape_html(slop_state_header)}</th>
                                    <th style="width: 15%; text-align: center;">Impact</th>
                                </tr>
                            </thead>
                            <tbody>
                                {scorecard_rows}
                            </tbody>
                        </table>
                    </div>
                </div>

                {remediation_html}
            </div>
        </main>
    </div>
    <script>
        function toggleFindingCard(headerEl) {{
            const card = headerEl.parentElement;
            const isCurrentlyCollapsed = card.classList.contains("is-collapsed");
            document.querySelectorAll(".finding-card").forEach(c => c.classList.add("is-collapsed"));
            if (isCurrentlyCollapsed) {{
                card.classList.remove("is-collapsed");
            }}
        }}

        function filterScorecard(sev) {{
            const rows = document.querySelectorAll("#scorecard table tbody tr");
            rows.forEach(r => {{
                if (sev === "all") {{
                    r.style.display = "";
                }} else {{
                    const badge = r.querySelector(".badge");
                    if (badge && badge.textContent.trim().toLowerCase().includes(sev.toLowerCase())) {{
                        r.style.display = "";
                    }} else {{
                        r.style.display = "none";
                    }}
                }}
            }});
            const cards = document.querySelectorAll(".finding-card");
            cards.forEach(c => {{
                if (sev === "all") {{
                    c.style.display = "";
                }} else {{
                    const badge = c.querySelector(".badge");
                    if (badge && badge.textContent.trim().toLowerCase().includes(sev.toLowerCase())) {{
                        c.style.display = "";
                    }} else {{
                        c.style.display = "none";
                    }}
                }}
            }});
        }}

        function copyRemediationPrompt() {{
            const textEl = document.getElementById("remediation-prompt-text");
            if (textEl) {{
                navigator.clipboard.writeText(textEl.innerText).then(() => {{
                    showToast("Remediation prompt copied to clipboard!");
                }}).catch(err => {{
                    console.error("Copy failed", err);
                }});
            }}
        }}

        function showToast(message) {{
            let toast = document.getElementById("dashboard-toast");
            if (!toast) {{
                toast = document.createElement("div");
                toast.id = "dashboard-toast";
                toast.className = "toast-notification";
                document.body.appendChild(toast);
            }}
            toast.textContent = message;
            toast.classList.add("show");
            setTimeout(() => {{
                toast.classList.remove("show");
            }}, 2500);
        }}

        document.addEventListener("DOMContentLoaded", function() {{
            const dateEl = document.getElementById("audit-date-time");
            if (dateEl) {{
                const utcStr = dateEl.getAttribute("data-utc");
                if (utcStr) {{
                    const d = new Date(utcStr);
                    const options = {{
                        year: 'numeric',
                        month: 'long',
                        day: 'numeric',
                        hour: '2-digit',
                        minute: '2-digit',
                        second: '2-digit',
                        hour12: true
                    }};
                    dateEl.textContent = d.toLocaleString(undefined, options);
                }}
            }}

            // Code windows toggling
            const codeWindows = document.querySelectorAll(".code-window");
            codeWindows.forEach(win => {{
                const header = win.querySelector(".code-header");
                if (header) {{
                    header.addEventListener("click", (e) => {{
                        e.stopPropagation();
                        win.classList.toggle("is-collapsed");
                    }});
                }}
            }});

            // Search filter
            const searchInput = document.getElementById("search-input");
            const findingCards = document.querySelectorAll(".finding-card");
            let currentSearch = "";

            function filterFindings() {{
                findingCards.forEach(card => {{
                    const cardText = card.textContent.toLowerCase();
                    const matchesSearch = (!currentSearch || cardText.includes(currentSearch));
                    card.style.display = matchesSearch ? "block" : "none";
                }});
            }}

            if (searchInput) {{
                searchInput.addEventListener("input", (e) => {{
                    currentSearch = e.target.value.toLowerCase().trim();
                    filterFindings();
                }});
            }}

            // Active sidebar navigation highlighting via IntersectionObserver
            const navLinks = document.querySelectorAll(".nav-item");
            const sections = [
                document.getElementById("overview"),
                document.getElementById("what-changed"),
                document.getElementById("findings"),
                document.getElementById("observations"),
                document.getElementById("scorecard"),
                document.getElementById("remediation")
            ].filter(Boolean);

            navLinks.forEach(link => {{
                link.addEventListener("click", () => {{
                    navLinks.forEach(l => l.classList.remove("active"));
                    link.classList.add("active");
                }});
            }});

            if (sections.length > 0) {{
                const observerOptions = {{
                    root: null,
                    rootMargin: "-20% 0px -60% 0px",
                    threshold: 0
                }};

                const observer = new IntersectionObserver((entries) => {{
                    entries.forEach(entry => {{
                        if (entry.isIntersecting) {{
                            const id = entry.target.getAttribute("id");
                            navLinks.forEach(link => {{
                                link.classList.remove("active");
                                if (link.getAttribute("href") === `#${{id}}`) {{
                                    link.classList.add("active");
                                }}
                            }});
                        }}
                    }});
                }}, observerOptions);

                sections.forEach(section => observer.observe(section));
            }}
        }});
    </script>
</body>
</html>
"""

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html_content)

def main():
    args = sys.argv[1:]
    if not args or any(a in ("-h", "--help") for a in args):
        print("Usage: python compile_audit.py <markdown_file_path> [output_file_path] [-y/--yes]", file=sys.stderr)
        sys.exit(1)

    auto_yes = False
    if "-y" in args:
        auto_yes = True
        args.remove("-y")
    if "--yes" in args:
        auto_yes = True
        args.remove("--yes")

    if not args:
        print("Usage: python compile_audit.py <markdown_file_path> [output_file_path] [-y/--yes]", file=sys.stderr)
        sys.exit(1)

    markdown_path = args[0]
    output_path = args[1] if len(args) >= 2 else None

    # Interactive confirmation if run interactively in a TTY and not --yes
    if not auto_yes and sys.stdin.isatty():
        try:
            choice = input(f"Compile '{markdown_path}' into interactive HTML audit report? [y/N]: ").strip().lower()
            if choice not in ("y", "yes"):
                print("Compilation aborted by user.")
                sys.exit(0)
        except (KeyboardInterrupt, EOFError):
            print("\nAborted.")
            sys.exit(0)

    print(f"Parsing markdown from {markdown_path}...")
    title, metadata, disclaimer, sections = parse_markdown(markdown_path)
    
    is_test_report = "test" in (title or "").lower()
    
    if output_path:
        previous_score = None
        previous_findings = []
        next_idx = 1
    else:
        # Auto-determine output path and load previous report info
        proj_root = os.path.abspath(os.path.dirname(markdown_path))
        output_path, previous_score, next_idx, previous_findings = get_next_audit_info(proj_root, is_test_report)
        print(f"Auto output path: {output_path} (audit #{next_idx})")
        if previous_score is not None:
            print(f"Previous score: {previous_score}/10, {len(previous_findings)} previous findings loaded for diff")
    
    print(f"Compiling HTML report to {output_path}...")
    generate_html_report(title, metadata, disclaimer, sections, output_path, 
                         previous_score=previous_score, previous_findings=previous_findings, next_idx=next_idx)
    print("Compilation successful!")
    
    # Clean up temporary markdown source file so only HTML files remain in audited dir
    try:
        if os.path.exists(markdown_path):
            os.remove(markdown_path)
            print(f"Cleaned up markdown source file: {markdown_path}")
    except Exception as e:
        print(f"Warning: Could not remove markdown file {markdown_path}: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()

