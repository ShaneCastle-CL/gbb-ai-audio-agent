# GitHub Pages Setup Instructions

## 📋 Required Repository Configuration

To enable GitHub Pages deployment for your documentation, you need to configure your repository settings:

### 1. Enable GitHub Pages

1. Go to your repository on GitHub: `https://github.com/pablosalvador10/gbb-ai-audio-agent`
2. Click on **Settings** tab
3. Scroll down to **Pages** section (in the left sidebar)
4. Under **Source**, select **GitHub Actions**
5. Click **Save**

### 2. Verify Workflow Permissions

1. In your repository **Settings**
2. Go to **Actions** → **General**
3. Under **Workflow permissions**, ensure:
   - ✅ **Read and write permissions** is selected
   - ✅ **Allow GitHub Actions to create and approve pull requests** is checked

### 3. Branch Protection (Optional but Recommended)

1. Go to **Settings** → **Branches**
2. Add a branch protection rule for `main`
3. Enable **Restrict pushes that create files larger than 100MB**

## 🚀 Deployment Process

Once configured, the documentation will automatically deploy when you:

1. **Push to main branch** → Builds and deploys to production
2. **Push to feature/improve_docs** → Builds and deploys for preview
3. **Create Pull Request** → Builds documentation (no deployment)

## 📖 Accessing Your Documentation

After successful deployment, your documentation will be available at:

```
https://pablosalvador10.github.io/gbb-ai-audio-agent/
```

## 🔧 Troubleshooting

### Common Issues:

1. **Pages not enabled**: Make sure GitHub Pages is set to "GitHub Actions" source
2. **Permission denied**: Check workflow permissions in repository settings
3. **Build fails**: Check the Actions tab for detailed error logs
4. **404 on site**: Wait 5-10 minutes after first deployment for DNS propagation

### Manual Trigger:

You can manually trigger the workflow by:
1. Going to **Actions** tab
2. Selecting **Deploy Documentation** workflow
3. Clicking **Run workflow**

## 📱 Local Testing

Before pushing, test your documentation locally:

```bash
# Install dependencies
pip install -r requirements-docs.txt
pip install -e .

# Serve documentation locally
./serve-docs.sh  # On Unix/macOS
# or
serve-docs.bat   # On Windows

# Open http://localhost:8000 in your browser
```

## ✅ Verification Checklist

- [ ] Repository Settings → Pages → Source set to "GitHub Actions"
- [ ] Repository Settings → Actions → General → Workflow permissions configured
- [ ] Workflow file `.github/workflows/docs.yml` exists
- [ ] Documentation dependencies in `requirements-docs.txt` exist
- [ ] Package setup in `setup.py` exists for mkdocstrings
- [ ] Local documentation builds successfully
- [ ] First deployment to GitHub Pages completed
