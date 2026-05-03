#!/bin/bash
set -e

echo "🚀 Starting Shellty Pulse..."

gunicorn --bind 0.0.0.0:5000 \
         --workers 1 \
         --threads 2 \
         --timeout 30 \
         --graceful-timeout 10 \
         --access-logfile - \
         app:app &

GUNICORN_PID=$!
echo "✓ Gunicorn started (PID: $GUNICORN_PID)"

echo "⏳ Waiting for application to start..."
sleep 10

MAX_ATTEMPTS=30
ATTEMPT=0
until curl -sf http://localhost:5000/health > /dev/null 2>&1 || [ $ATTEMPT -eq $MAX_ATTEMPTS ]; do
    echo "   Waiting for app... ($ATTEMPT/$MAX_ATTEMPTS)"
    sleep 2
    ATTEMPT=$((ATTEMPT+1))
done

if [ $ATTEMPT -eq $MAX_ATTEMPTS ]; then
    echo "❌ ERROR: Application failed to start"
    kill $GUNICORN_PID 2>/dev/null || true
    exit 1
fi

echo "✓ Application is ready!"
echo ""
echo "📊 Adding services to monitor..."

# KSeF Master
curl -sf -X POST http://localhost:5000/api/services \
  -H "Content-Type: application/json" \
  -d '{"name":"KSeF Master","url":"https://ksef-master-backend.onrender.com/health","frontend_url":"https://ksef-master.netlify.app/","interval":300}' \
  && echo "  ✓ KSeF Master" || echo "  ✗ KSeF Master FAILED"

# Postlio
curl -sf -X POST http://localhost:5000/api/services \
  -H "Content-Type: application/json" \
  -d '{"name":"Postlio","url":"https://postlio-backend.onrender.com/health","frontend_url":"https://postlio.netlify.app/","interval":300}' \
  && echo "  ✓ Postlio" || echo "  ✗ Postlio FAILED"

# Shellty Blog
curl -sf -X POST http://localhost:5000/api/services \
  -H "Content-Type: application/json" \
  -d '{"name":"Shellty Blog","url":"https://shellty-blog.onrender.com/health","frontend_url":"https://shellty-blog.onrender.com/","interval":300}' \
  && echo "  ✓ Shellty Blog" || echo "  ✗ Shellty Blog FAILED"

# SmartQuote AI
curl -sf -X POST http://localhost:5000/api/services \
  -H "Content-Type: application/json" \
  -d '{"name":"SmartQuote AI","url":"https://smartquote-backend-fzh5.onrender.com/api/health","frontend_url":"https://smartquote-ai.netlify.app/","interval":300}' \
  && echo "  ✓ SmartQuote AI" || echo "  ✗ SmartQuote AI FAILED"

# Shellty Kanban
curl -sf -X POST http://localhost:5000/api/services \
  -H "Content-Type: application/json" \
  -d '{"name":"Shellty Kanban","url":"https://shellty-kanban.onrender.com/health","frontend_url":"https://shellty-kanban.netlify.app/","interval":300}' \
  && echo "  ✓ Shellty Kanban" || echo "  ✗ Shellty Kanban FAILED"

# Shellty Pulse (self-monitor)
curl -sf -X POST http://localhost:5000/api/services \
  -H "Content-Type: application/json" \
  -d '{"name":"Shellty Pulse","url":"https://shellty-pulse.onrender.com/health","interval":300}' \
  && echo "  ✓ Shellty Pulse" || echo "  ✗ Shellty Pulse FAILED"

echo ""
echo "✅ All services initialized!"
echo "🌐 Shellty Pulse is running on http://0.0.0.0:5000"
echo ""

wait $GUNICORN_PID