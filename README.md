# Assetra

Assetra is a Django-based law-firm estate and asset management system. It includes a server-rendered web interface for daily firm operations and a Django REST Framework API for structured backend access.

## Features

- Firm-scoped user, client, beneficiary, asset, and document management
- Estate distribution approval workflow
- Audit log tracking for important actions
- Web dashboard and internal admin-style pages
- REST API with OpenAPI schema support

## Tech Stack

- Python
- Django 5.2
- Django REST Framework
- drf-spectacular
- SQLite for local development

## Project Structure

```text
Assetra/
├── config/              # Django project settings and root URLs
├── core/                # Main app: models, API views, web views, forms, permissions
├── static/              # CSS and images
├── templates/           # Django templates
├── media/               # Uploaded files in local development
├── manage.py
└── requirements.txt
```

## Setup

1. Clone the repository:

```bash
git clone https://github.com/krishAI04/assetra.git
cd assetra
```

2. Create and activate a virtual environment:

Windows PowerShell:

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
```

macOS/Linux:

```bash
python -m venv venv
source venv/bin/activate
```

3. Install dependencies:

```bash
pip install -r requirements.txt
```

4. Apply migrations:

```bash
python manage.py migrate
```

5. Create a superuser if needed:

```bash
python manage.py createsuperuser
```

6. Run the development server:

```bash
python manage.py runserver
```

7. Open the app:

- Web UI: `http://127.0.0.1:8000/`
- Admin: `http://127.0.0.1:8000/admin/`
- API schema: `http://127.0.0.1:8000/api/schema/`
- API docs: `http://127.0.0.1:8000/api/docs/`

## How To Use

### Web App

1. Sign up to create a firm workspace and the first admin account.
2. Log in with your account.
3. Use the dashboard to navigate to clients, documents, approvals, users, and audit log.
4. Add clients first, then attach assets, beneficiaries, and documents to those clients.
5. Review pending approvals from the approvals page.

### API

After logging in, the API is available under `/api/`.

Example endpoints:

- `/api/clients/`
- `/api/assets/`
- `/api/beneficiaries/`
- `/api/documents/`
- `/api/asset-distributions/`
- `/api/audit-logs/`

## Notes

- The project uses SQLite by default for local development.
- Uploaded files are stored in the local `media/` directory.
- Password validation is enabled and password hashing is configured through Django settings.

## Development Notes

- Run checks:

```bash
python manage.py check
```

- Generate migrations after model changes:

```bash
python manage.py makemigrations
python manage.py migrate
```

## License

This project is provided for educational and development use.
