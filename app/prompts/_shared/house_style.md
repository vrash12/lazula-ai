{% if house_style.rules or house_style.always_tags or house_style.avoid_words %}
House style for this client:
{% for rule in house_style.rules %}
- {{ rule }}
{% endfor %}
{% if house_style.always_tags %}
- Always include these hashtags: {{ house_style.always_tags | join(" ") }}
{% endif %}
{% if house_style.avoid_words %}
- Never use these words or characters: {{ house_style.avoid_words | join(", ") }}
{% endif %}
{% endif %}
