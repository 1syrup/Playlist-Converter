import os
import requests
import urllib.parse
import time
from datetime import datetime, timedelta
from flask import Flask, redirect, request, jsonify, session, url_for
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from oauthlib.oauth2 import WebApplicationClient
from urllib.parse import quote
from googleapiclient.errors import HttpError

os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'
app = Flask(__name__)
app.secret_key = "dingtone"

#SPOTIFY OAUTH CREDS
CLIENT_ID = "18b2aa38f6804350aecec03a75ee8af5"
CLIENT_SECRET =
REDIRECT_URI = "http://localhost:5000/callback"
AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
API_BASE_URL = "https://api.spotify.com/v1/"

#GOOGLE OAUTH CREDS
GOOGLE_CLIENT_ID = "515386421135-uqp27sgordktlfe65fi0t290vq8r9vcj.apps.googleusercontent.com"
GOOGLE_CLIENT_SECRET =
GOOGLE_REDIRECT_URI = "http://localhost:5000/callback-google"
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPES = [
    'https://www.googleapis.com/auth/youtube.readonly',
    'https://www.googleapis.com/auth/youtube.force-ssl'
]

@app.route("/")
def index():
    spotify_logged_in = 'access_token' in session
    youtube_logged_in = 'google_credentials' in session

    html = '''
    <h1>Welcome to Tunevert</h1>
    <img src="/static/tunevert.png" alt="Playlist App Image" style="width:200px;height:auto;">
    '''

    if spotify_logged_in:
        html += '<p>You are currently logged in to Spotify</p>'
    else:
        html += '<p><a href="/login">Login with Spotify</a></p>'

    if youtube_logged_in:
        html += '<p>You are currently logged in to YouTube Music</p>'
    else:
        html += '<p><a href="/login-google">Login with YouTube Music</a></p>'

    if spotify_logged_in or youtube_logged_in:
        html += '<p><a href="/playlists">Go to Playlists</a></p>'
        html += '<p><a href="/logout-all">Logout from All Platforms</a></p>'

    return html

@app.route("/login")
def login():
    scope = "user-read-private user-read-email playlist-modify-public playlist-modify-private"

    params = {
        "client_id": CLIENT_ID,
        "response_type": "code",
        "scope" : scope,
        "redirect_uri" : REDIRECT_URI,

        #false default
        "show_dialog" : True
    }

    auth_url = f"{AUTH_URL}?{urllib.parse.urlencode(params)}"
    return redirect(auth_url)

@app.route("/login-google")
def login_google():
    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "auth_uri": GOOGLE_AUTH_URL,
                "token_uri": GOOGLE_TOKEN_URL,
                "redirect_uris": [GOOGLE_REDIRECT_URI]
            }
        },
        scopes=SCOPES
    )
    flow.redirect_uri = GOOGLE_REDIRECT_URI
    authorization_url, state = flow.authorization_url(
        access_type='offline',
        include_granted_scopes='true'
    )
    session['state'] = state
    return redirect(authorization_url)

@app.route("/callback")
def callback():
    if "error" in request.args:
        return jsonify({"error": request.args["error"]})
    
    if "code" in request.args:
        req_body = {
            "code" : request.args['code'],
            "grant_type" : "authorization_code",
            "client_id" : CLIENT_ID,
            "client_secret" : CLIENT_SECRET,
            "redirect_uri" : REDIRECT_URI
        }
        response = requests.post(TOKEN_URL, data = req_body)
        token_info = response.json()

        session["access_token"] = token_info["access_token"]
        session["refresh_token"] = token_info["refresh_token"]
        #expires in 3600
        session["expires_at"] = datetime.now().timestamp() + token_info["expires_in"]

        return redirect("/playlists")
    
@app.route("/callback-google")
def callback_google():
    state = session['state']
    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "auth_uri": GOOGLE_AUTH_URL,
                "token_uri": GOOGLE_TOKEN_URL,
                "redirect_uris": [GOOGLE_REDIRECT_URI]
            }
        },
        scopes=SCOPES,
        state=state
    )
    flow.redirect_uri = GOOGLE_REDIRECT_URI
    
    flow.fetch_token(authorization_response=request.url)

    credentials = flow.credentials
    session['google_credentials'] = {
        'token': credentials.token,
        'refresh_token': credentials.refresh_token,
        'token_uri': credentials.token_uri,
        'client_id': credentials.client_id,
        'client_secret': credentials.client_secret,
        'scopes': credentials.scopes
    }

    return redirect("/playlists")

#return google credentials
def credentials_to_dict(credentials):
    return {
        'token': credentials.token,
        'refresh_token': credentials.refresh_token,
        'token_uri': credentials.token_uri,
        'client_id': credentials.client_id,
        'client_secret': credentials.client_secret,
        'scopes': credentials.scopes
    }

#helper function to check session validity
def check_session_and_get_headers():
    if "access_token" not in session:
        return redirect("/login"), None
    
    if datetime.now().timestamp() > session["expires_at"]:
        return redirect("/refresh-token"), None
    
    headers = {
        "Authorization": f"Bearer {session['access_token']}"
    }
    
    return None, headers

from googleapiclient.discovery import build

def youtube_request_with_backoff(request, max_retries=5):
    for attempt in range(max_retries):
        try:
            return request.execute()
        except HttpError as e:
            if e.resp.status in [403, 500, 503] and attempt < max_retries - 1:
                delay = 2 ** attempt
                print(f"YouTube API request failed. Retrying in {delay} seconds...")
                time.sleep(delay)
            else:
                raise

@app.route("/playlists")
def get_playlists():
    result = []

    # Add "Back to Home" link at the top
    result.append('<a href="/">Back to Home</a>')
    result.append('<h1>Your Playlists</h1>')

    # Check Spotify login
    redirect_response, spotify_headers = check_session_and_get_headers()
    if redirect_response:
        return redirect_response
    
    # Get Spotify user profile
    spotify_profile = get_user_profile(spotify_headers)
    if spotify_profile:
        result.append(f"<h2>Spotify login: {spotify_profile['display_name']}</h2>")

    # Check Google login
    if 'google_credentials' in session:
        google_credentials = Credentials(**session['google_credentials'])
        youtube = build('youtube', 'v3', credentials=google_credentials)
        
        # Get Google user info
        channel_request = youtube.channels().list(part="snippet", mine=True)
        channel_response = channel_request.execute()
        if 'items' in channel_response and channel_response['items']:
            channel = channel_response['items'][0]['snippet']
            result.append(f"<h2>YouTube login: {channel['title']}</h2>")
        
        result.append("<h3>Your YouTube Playlists:</h3>")
        playlist_request = youtube.playlists().list(part="snippet", mine=True, maxResults=50)
        try:
            playlist_response = youtube_request_with_backoff(playlist_request)
            for item in playlist_response.get('items', []):
                playlist_name = item['snippet']['title']
                playlist_id = item['id']
                playlist_link = f'<a href="/youtube-tracks/{playlist_id}/{playlist_name}">{playlist_name}</a>'
                result.append(playlist_link)
        except HttpError as e:
            result.append(f"Error fetching YouTube playlists: {str(e)}")

    # Get Spotify playlists
    response = requests.get(API_BASE_URL + "me/playlists", headers=spotify_headers)
    playlists = response.json()
    
    result.append("<h3>Your Spotify Playlists:</h3>")
    for element in playlists["items"]:
        playlist_name = element['name']
        playlist_id = element['id']
        playlist_link = f'<a href="/tracks/{playlist_id}/{playlist_name}">{playlist_name}</a>'
        result.append(playlist_link)

    s = '<br>'.join(result)

    return s

@app.route("/tracks/<playlist_id>/<playlist_name>")
def get_tracks(playlist_id,playlist_name):
    redirect_response, headers = check_session_and_get_headers()
    if redirect_response:
        return redirect_response

    #get the tracks for the chosen playlist from the json and return string
    response = requests.get(f"{API_BASE_URL}playlists/{playlist_id}/tracks", headers=headers)
    if response.status_code != 200:
        return f"Error: Unable to fetch tracks. Status code: {response.status_code}"
    tracks = response.json()

    result = []
    for element in tracks['items']:
        track = element['track']
        track_name = track['name']
        artists = ", ".join([artist['name'] for artist in track['artists']])
        result.append(f"{track_name} - {artists}")

    tracks_string = "<br>".join(result)

    #copy playlist button
    copy_link = f'<br><br><a href="/copy-playlist/spotify/{playlist_id}/{playlist_name}">Copy Playlist</a>'

    # Add logout link and back to playlists link
    back_link = '<br><a href="/playlists">Back to Playlists</a>'
    return f"<h2>Tracks in playlist {playlist_name}</h2>{tracks_string}{copy_link}{back_link}"

@app.route("/youtube-tracks/<playlist_id>/<playlist_name>")
def get_youtube_tracks(playlist_id, playlist_name):
    if 'google_credentials' not in session:
        return redirect("/login-google")

    google_credentials = Credentials(**session['google_credentials'])
    youtube = build('youtube', 'v3', credentials=google_credentials)

    result = [f"<h2>Tracks in YouTube playlist: {playlist_name}</h2>"]

    next_page_token = None
    while True:
        request = youtube.playlistItems().list(
            part="snippet",
            playlistId=playlist_id,
            maxResults=50,
            pageToken=next_page_token
        )
        response = request.execute()

        for item in response['items']:
            video_title = item['snippet']['title']
            video_id = item['snippet']['resourceId']['videoId']
            channel_title = item['snippet']['videoOwnerChannelTitle']
            track_link = f'<a href="https://www.youtube.com/watch?v={video_id}" target="_blank">{video_title}</a>'
            result.append(f"{track_link} - {channel_title}")

        next_page_token = response.get('nextPageToken')
        if not next_page_token:
            break

    tracks_string = "<br>".join(result)
    
    # Add "Copy Playlist" link
    copy_link = f'<br><br><a href="/copy-playlist/youtube/{playlist_id}/{playlist_name}">Copy Playlist</a>'
    
    # Add back to playlists link
    back_link = '<br><a href="/playlists">Back to Playlists</a>'
    
    return tracks_string + copy_link + back_link

#copy playlist page
@app.route("/copy-playlist/<source_platform>/<playlist_id>/<playlist_name>")
def copy_playlist(source_platform, playlist_id, playlist_name):
    available_platforms = []
    
    if "access_token" in session:
        available_platforms.append("spotify")
    
    if "google_credentials" in session:
        available_platforms.append("youtube")
    
    if len(available_platforms) < 2:
        return "You need to be logged in to both platforms to copy playlists."
    
    target_platform = "spotify" if source_platform == "youtube" else "youtube"
    
    html = f"""
    <h2>Copy Playlist: {playlist_name}</h2>
    <p>Source: {source_platform}</p>
    <p>Target: {target_platform}</p>
    <form action="/perform-copy" method="post">
        <input type="hidden" name="source_platform" value="{source_platform}">
        <input type="hidden" name="target_platform" value="{target_platform}">
        <input type="hidden" name="playlist_id" value="{playlist_id}">
        <input type="hidden" name="playlist_name" value="{playlist_name}">
        <input type="submit" value="Confirm Copy">
    </form>
    <br>
    <a href="/playlists">Back to Playlists</a>
    """
    
    return html

#perform the copying of playlist
@app.route("/perform-copy", methods=["POST"])
def perform_copy():
    source_platform = request.form["source_platform"]
    target_platform = request.form["target_platform"]
    playlist_id = request.form["playlist_id"]
    playlist_name = request.form["playlist_name"]

    if source_platform == "spotify" and target_platform == "youtube":
        return copy_spotify_to_youtube(playlist_id, playlist_name)
    elif source_platform == "youtube" and target_platform == "spotify":
        return copy_youtube_to_spotify(playlist_id, playlist_name)
    else:
        return "Invalid platform combination"

def copy_spotify_to_youtube(playlist_id, playlist_name):
    # Fetch Spotify tracks
    spotify_headers = {"Authorization": f"Bearer {session['access_token']}"}
    response = requests.get(f"{API_BASE_URL}playlists/{playlist_id}/tracks", headers=spotify_headers)
    if response.status_code != 200:
        error_message = response.json().get('error', {}).get('message', 'Unknown error')
        return f"Error: Unable to fetch Spotify tracks. Status code: {response.status_code}. Message: {error_message}"
    
    spotify_tracks = response.json()['items']

    # Create YouTube playlist
    try:
        google_credentials = Credentials(**session['google_credentials'])
        youtube = build('youtube', 'v3', credentials=google_credentials)
        
        new_playlist = youtube.playlists().insert(
            part="snippet,status",
            body={
                "snippet": {
                    "title": f"{playlist_name} (from Spotify)",
                    "description": "Playlist copied from Spotify"
                },
                "status": {
                    "privacyStatus": "public"
                }
            }
        ).execute()
    except HttpError as e:
        return f"Error: Unable to create YouTube playlist. {e.resp.status}: {e.content}"

    new_playlist_id = new_playlist['id']

    # Add tracks to YouTube playlist
    added_tracks = 0
    failed_tracks = 0
    for track in spotify_tracks:
        track_name = track['track']['name']
        artists = ", ".join([artist['name'] for artist in track['track']['artists']])
        search_query = f"{track_name} {artists}"
        
        try:
            search_response = youtube.search().list(
                q=search_query,
                type="video",
                part="id",
                maxResults=1
            ).execute()

            if search_response['items']:
                video_id = search_response['items'][0]['id']['videoId']
                youtube.playlistItems().insert(
                    part="snippet",
                    body={
                        "snippet": {
                            "playlistId": new_playlist_id,
                            "resourceId": {
                                "kind": "youtube#video",
                                "videoId": video_id
                            }
                        }
                    }
                ).execute()
                added_tracks += 1
            else:
                failed_tracks += 1
        except HttpError as e:
            failed_tracks += 1
            print(f"Error adding track '{track_name}': {e.resp.status}: {e.content}")

    result = f"Playlist '{playlist_name}' copied from Spotify to YouTube.\n"
    result += f"New YouTube playlist ID: {new_playlist_id}\n"
    result += f"Successfully added tracks: {added_tracks}\n"
    result += f"Failed to add tracks: {failed_tracks}"

    return result

def copy_youtube_to_spotify(playlist_id, playlist_name):
    # Fetch YouTube tracks
    google_credentials = Credentials(**session['google_credentials'])
    youtube = build('youtube', 'v3', credentials=google_credentials)

    youtube_tracks = []
    next_page_token = None
    while True:
        playlist_items = youtube.playlistItems().list(
            part="snippet",
            playlistId=playlist_id,
            maxResults=50,
            pageToken=next_page_token
        ).execute()

        youtube_tracks.extend(playlist_items['items'])
        next_page_token = playlist_items.get('nextPageToken')
        if not next_page_token:
            break

    # Create Spotify playlist
    spotify_headers = {"Authorization": f"Bearer {session['access_token']}"}
    user_profile = get_user_profile(spotify_headers)
    if not user_profile:
        return "Error: Unable to fetch Spotify user profile. Please try logging in again."
    
    user_id = user_profile['id']

    create_playlist_response = requests.post(
        f"{API_BASE_URL}users/{user_id}/playlists",
        headers=spotify_headers,
        json={
            "name": f"{playlist_name} (from YouTube)",
            "description": "Playlist copied from YouTube",
            "public": True
        }
    )
    if create_playlist_response.status_code != 201:
        error_message = create_playlist_response.json().get('error', {}).get('message', 'Unknown error')
        return f"Error: Unable to create Spotify playlist. Status code: {create_playlist_response.status_code}. Message: {error_message}"

    new_playlist = create_playlist_response.json()
    new_playlist_id = new_playlist['id']

    # Add tracks to Spotify playlist
    track_uris = []
    for track in youtube_tracks:
        video_title = track['snippet']['title']
        search_query = quote(video_title)
        search_response = requests.get(
            f"{API_BASE_URL}search?q={search_query}&type=track&limit=1",
            headers=spotify_headers
        )
        if search_response.status_code == 200 and search_response.json()['tracks']['items']:
            track_uri = search_response.json()['tracks']['items'][0]['uri']
            track_uris.append(track_uri)

    # Add tracks in batches of 100 (Spotify API limit)
    for i in range(0, len(track_uris), 100):
        batch = track_uris[i:i+100]
        add_tracks_response = requests.post(
            f"{API_BASE_URL}playlists/{new_playlist_id}/tracks",
            headers=spotify_headers,
            json={"uris": batch}
        )
        if add_tracks_response.status_code != 201:
            error_message = add_tracks_response.json().get('error', {}).get('message', 'Unknown error')
            return f"Error: Unable to add tracks to Spotify playlist. Status code: {add_tracks_response.status_code}. Message: {error_message}"

    return f"Playlist '{playlist_name}' copied from YouTube to Spotify. New Spotify playlist ID: {new_playlist_id}"

@app.route("/refresh-token")
def refresh_token():
    if "refresh_token" not in session:
        return redirect("/login")
    if datetime.now().timestamp() > session["expires_at"]:
        req_body = {
            "grant_type": "refresh_token",
            "refresh_token": session["refresh_token"],
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET
        }
    
        response = requests.post(TOKEN_URL, data=req_body)
        new_token_info = response.json()
        session["access_token"] = new_token_info["access_token"]
        session["expires_at"] = datetime.now().timestamp() + new_token_info["expires_in"]

        return redirect("/playlists")
    
#get currently logged in user
def get_user_profile(headers):
    response = requests.get(API_BASE_URL + "me", headers=headers)
    if response.status_code == 200:
        return response.json()
    return None
    
@app.route("/logout-all")
def logout_all():
    session.pop('access_token', None)
    session.pop('refresh_token', None)
    session.pop('expires_at', None)
    session.pop('google_credentials', None)
    return redirect("/")
    
if __name__ == "__main__":
    os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'  # For development only
    app.run(host = "0.0.0.0", debug = True)
