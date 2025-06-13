from flask import Flask, request, render_template, redirect, url_for, send_file, abort
import uuid # For generating unique IDs
import io # For in-memory file for download

# Import the core logic functions
from m3u_epg_core import (
    fetch_content, 
    check_m3u, 
    apply_m3u_fixes, 
    check_epg # Assuming you might use check_epg in a cron job too.
)

app = Flask(__name__)

# --- Temporary storage for fixed files ---
# In a production environment with multiple workers or servers,
# this would need to be a more robust solution like a database,
# Redis, or temporary file storage with cleanup.
# For this simple Flask app, an in-memory dictionary is sufficient
# but will clear on server restart.
app.temp_fixed_files = {} # Stores {file_id: bytes_content}

# --- M3U-EPG Compatibility Checker (remains in app.py as it uses both types of data) ---
def check_m3u_epg_compatibility(m3u_channels, epg_channels):
    """
    Checks compatibility between M3U and EPG data for Channels DVR,
    separating specific issues from general advice.
    Returns (list_of_compatibility_issues, list_of_channels_dvr_advice).
    """
    compatibility_issues = [] # Specific issues (warnings/errors)
    channels_dvr_advice = []   # General advice/best practices

    m3u_tvg_ids = {c['tvg_id'] for c in m3u_channels if c['tvg_id']}
    epg_channel_ids = set(epg_channels.keys())

    # 1. Channels in M3U without matching EPG data
    for m3u_channel in m3u_channels:
        if m3u_channel['tvg_id']:
            if m3u_channel['tvg_id'] not in epg_channel_ids:
                compatibility_issues.append(f"Compatibility Warning: M3U channel '{m3u_channel['name']}' (tvg-id: '{m3u_channel['tvg_id']}') has no matching EPG data found by 'tvg-id'. This channel might not show guide data in Channels DVR.")
        else:
            pass # We rely on check_m3u to report missing tvg-id


    # 2. EPG channels without matching M3U data
    for epg_id in epg_channel_ids:
        if epg_id not in m3u_tvg_ids:
            display_names = epg_channels.get(epg_id, {}).get('display_names', ['N/A'])
            compatibility_issues.append(f"Compatibility Warning: EPG channel '{', '.join(display_names)}' (id: '{epg_id}') has no matching M3U channel via 'tvg-id'. This EPG data will not be used by Channels DVR.")

    # 3. General Channels DVR compatibility advice
    channels_dvr_advice.append("For optimal guide data, ensure 'tvg-id' in M3U *exactly* matches 'id' in EPG (case-sensitive).")
    channels_dvr_advice.append("Missing or inconsistent 'tvg-id' attributes are the most common reason for guide data not showing up.")
    channels_dvr_advice.append("Duplicate 'tvg-id' values in M3U can cause unpredictable channel importing in Channels DVR.")
    channels_dvr_advice.append("Ensure your EPG file includes essential program details like `<title>`, `<desc>`, `series-id` (for TV shows), and `episode-num` for best DVR functionality.")
    channels_dvr_advice.append("Overlapping program times in EPG for a single channel can lead to incorrect guide display or recording issues.")
    channels_dvr_advice.append("Channels DVR prefers HLS (.m3u8) or raw MPEG-TS (.ts) streams. Other formats might have limited or no support.")
    channels_dvr_advice.append("Consider adding a 'group-title' to your M3U channels to organize them into categories in Channels DVR's UI.")
    channels_dvr_advice.append("Remember: Channels DVR *can* display guide data without an external EPG file if your M3U channels use `tvg-id`s that map to known Gracenote IDs. Otherwise, an external EPG source is required.")

    return compatibility_issues, channels_dvr_advice


# --- Flask Routes ---
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload_file():
    m3u_file = request.files.get('m3u_file')
    m3u_url = request.form.get('m3u_url')

    epg_file = request.files.get('epg_file')
    epg_url = request.form.get('epg_url')

    m3u_content = None
    m3u_errors = []
    epg_content = None
    epg_errors = []

    # Determine M3U source and fetch content
    if m3u_file and m3u_file.filename:
        if not m3u_file.filename.lower().endswith(('.m3u', '.m3u8')):
            m3u_errors.append("Invalid M3U file extension. Please upload a .m3u or .m3u8 file.")
        else:
            fetched_content, fetch_msgs = fetch_content('file', m3u_file)
            m3u_content = fetched_content
            m3u_errors.extend(fetch_msgs)
    elif m3u_url:
        fetched_content, fetch_msgs = fetch_content('url', m3u_url)
        m3u_content = fetched_content
        m3u_errors.extend(fetch_msgs)
    else:
        m3u_errors.append("No M3U file or URL provided.")


    # Determine EPG source and fetch content
    if epg_file and epg_file.filename:
        if not (epg_file.filename.lower().endswith('.xml') or epg_file.filename.lower().endswith('.xmltv')):
            epg_errors.append("Invalid EPG file extension. Please upload a .xml or .xmltv file.")
        else:
            fetched_content, fetch_msgs = fetch_content('file', epg_file)
            epg_content = fetched_content
            epg_errors.extend(fetch_msgs)
    elif epg_url:
        fetched_content, fetch_msgs = fetch_content('url', epg_url)
        epg_content = fetched_content
        epg_errors.extend(fetch_msgs)


    m3u_channels_data = []
    epg_channels_data = {}
    epg_programs_data = []
    m3u_epg_compat_issues = []
    channels_dvr_advice = []
    
    m3u_fix_suggestions = []
    fixed_m3u_content = None
    fixed_file_id = None # Initialize file ID

    # Only run M3U analysis if content was successfully fetched
    if m3u_content:
        m3u_errors_analysis, m3u_channels_data, m3u_fix_suggestions = check_m3u(m3u_content)
        m3u_errors.extend(m3u_errors_analysis)
        
        # Now apply fixes if suggestions exist
        if m3u_fix_suggestions:
            fixed_m3u_content = apply_m3u_fixes(m3u_content, m3u_fix_suggestions)
            # Store fixed content temporarily and generate an ID
            fixed_file_id = str(uuid.uuid4())
            app.temp_fixed_files[fixed_file_id] = fixed_m3u_content.encode('utf-8')
        else:
            fixed_m3u_content = None # No fixes to apply
    

    # Only run EPG analysis if content was successfully fetched
    if epg_content:
        epg_errors_analysis, epg_channels_data, epg_programs_data = check_epg(epg_content)
        epg_errors.extend(epg_errors_analysis)
    
    # Run compatibility checks and collect general advice
    if m3u_content and epg_content:
        m3u_epg_compat_issues, channels_dvr_advice = check_m3u_epg_compatibility(m3u_channels_data, epg_channels_data)
    elif m3u_content and (not epg_content and not epg_errors): # M3U provided, EPG not provided/failed
        m3u_epg_compat_issues.append("Compatibility Note: No EPG file or valid EPG URL was provided.")
        channels_dvr_advice.append("Channels DVR *can* display guide data without an external EPG source if your M3U channels use `tvg-id`s that map to known Gracenote IDs. Otherwise, an external EPG source is required.")
    elif epg_content and (not m3u_content and not m3u_errors): # EPG provided, M3U not provided/failed
        m3u_epg_compat_issues.append("Compatibility Note: No M3U file or valid M3U URL was provided.")
        channels_dvr_advice.append("EPG data alone is not sufficient for Channels DVR; it needs to be linked to channels in a playlist (via `tvg-id`).")

    return render_template('results.html',
                           m3u_errors=m3u_errors,
                           epg_errors=epg_errors,
                           m3u_channels=m3u_channels_data,
                           epg_channels=epg_channels_data,
                           m3u_epg_compat_issues=m3u_epg_compat_issues,
                           channels_dvr_advice=channels_dvr_advice,
                           fixed_m3u_content=fixed_m3u_content, # For display in <pre> tag
                           m3u_fix_suggestions_count=len(m3u_fix_suggestions),
                           fixed_file_id=fixed_file_id # Pass the file ID for download
                           )

# --- Download route for fixed M3U ---
@app.route('/download_fixed_m3u/<file_id>', methods=['GET'])
def download_fixed_m3u(file_id):
    """
    Handles the download of the fixed M3U file using a unique ID.
    """
    fixed_content_bytes = app.temp_fixed_files.get(file_id)

    if fixed_content_bytes is None:
        app.logger.error(f"Attempted to download non-existent file_id: {file_id}")
        return abort(404, description="File not found or expired.")
    
    try:
        buffer = io.BytesIO(fixed_content_bytes)
        buffer.seek(0)
        return send_file(
            buffer,
            mimetype='application/x-mpegurl', # Standard MIME type for M3U playlists
            as_attachment=True,
            download_name='fixed_playlist.m3u' # Suggested filename for download
        )
    except Exception as e:
        app.logger.error(f"An internal error occurred while generating the fixed file for download (ID: {file_id}): {e}")
        return f"An internal error occurred while generating the fixed file: {e}", 500


if __name__ == '__main__':
    # Set a more appropriate host for Docker deployment
    app.run(debug=False, host='0.0.0.0')
