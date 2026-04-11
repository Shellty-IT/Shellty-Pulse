gunicorn --bind 0.0.0.0:8000 \
         --workers 1 \
         --threads 2 \
         --timeout 30 \
         --chdir /home/site/wwwroot \
         --pythonpath /home/site/wwwroot \
         app:app