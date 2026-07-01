#!/bin/bash

# Check if code was compiled
if [ ! -f "/app/compiled_flag" ]; then
  /app/bin/python3 manage.py tailwind plugin_install @tailwindcss/forms \
  && /app/bin/python3 manage.py tailwind plugin_install @tailwindcss/typography \
  && /app/bin/python3 manage.py tailwind plugin_install daisyui\
  && /app/bin/python3 manage.py tailwind build \
  && /app/bin/python3 manage.py compilemessages --use-fuzzy\
  && /app/bin/python3 manage.py collectstatic --noinput \
  && /app/bin/python3 manage.py makemigrations \
  && /app/bin/python3 manage.py migrate \
  && echo "This file indicates that the code was already compiled" > /app/compiled_flag
fi

# Check if DB exists
if [ ! -f "/app/database/admin_flag" ]; then
  /app/bin/python3 manage.py createsuperuser --noinput \
  && echo "This file indicates that an admin is already created" > /app/database/admin_flag
fi

# Web process
exec /app/bin/gunicorn comea_website.wsgi:application
