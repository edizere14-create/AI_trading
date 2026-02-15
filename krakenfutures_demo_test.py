import ccxt  # type: ignore

exchange = ccxt.krakenfutures({
    'apiKey': 'WLadp6jPkjij53gR84YmUNfFdLZ2KebLX85lAMiIxj6FOdn/wOxWvvfP',
    'secret': '+apZQXS02v5y3I19+O9ssHJKmDcYIE4PG993qMzHwpnTEoUzg+t4AT3FFl0i3vfJmnUTWT0B4cYP5lAPAJvMndBE',
    'sandbox': True,
    'urls': {'api': {'public': 'https://demo-futures.kraken.com/api'}},
})

try:
    balance = exchange.fetch_balance()
    print('Balance:', balance)
except Exception as e:
    print('Error:', e)
