{
  "log": { "loglevel": "warning" },
  "inbounds": [
    {
      "port": 10808,
      "listen": "0.0.0.0",
      "protocol": "socks",
      "settings": { "udp": true, "auth": "noauth" },
      "tag": "socks-in"
    },
    {
      "port": 10809,
      "listen": "0.0.0.0",
      "protocol": "http",
      "settings": {},
      "tag": "http-in"
    }
  ],
  "outbounds": [
    {
      "protocol": "vless",
      "settings": {
        "vnext": [
          {
            "address": "__XRAY_HOST__",
            "port": 443,
            "users": [
              { "id": "__XRAY_UUID__", "encryption": "none" }
            ]
          }
        ]
      },
      "streamSettings": {
        "network": "ws",
        "security": "tls",
        "tlsSettings": {
          "serverName": "__XRAY_HOST__",
          "allowInsecure": false,
          "fingerprint": "chrome",
          "alpn": ["http/1.1"]
        },
        "wsSettings": {
          "path": "/ws",
          "headers": { "Host": "__XRAY_HOST__" }
        }
      }
    }
  ]
}