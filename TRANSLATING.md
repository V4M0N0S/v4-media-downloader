# Translating V4 Media Downloader

Translations live in the `locales/` folder. The application discovers every valid `*.json` file in that folder automatically and adds it to the language selector in the footer.

## Add a language
1. Copy `locales/en.json` to a new file, for example `locales/fr.json`.
2. Set `meta.code` to the exact filename without `.json`.
3. Set `meta.name`, `meta.native_name` and `meta.locale`.
4. Translate the values. Do not rename translation keys.
5. Keep placeholders such as `{count}` and `{name}` unchanged.
6. Restart the service after adding the file.

Example metadata:

```json
"meta": {
  "code": "fr",
  "name": "French",
  "native_name": "Français",
  "locale": "fr-FR"
}
```

No Python, HTML or JavaScript changes are required for an additional language.
Feel free to open a pull request if you wanna help translate this project.