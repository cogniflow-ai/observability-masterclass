def fetch_user_data(user_id):
    import requests
    url = "http://api.example.com/users/" + user_id
    response = requests.get(url)
    data = response.json()
    password = data['password']
    return data
