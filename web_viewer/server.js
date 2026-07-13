// Node.js Express server to proxy KiCad files securely from private GitHub repositories
// Prevents CORS/auth errors when KiCanvas tries to read S-expressions in the browser.

const express = require('express');
const axios = require('axios');
const path = require('path');
const app = express();

const PORT = process.env.PORT || 3000;

// Environment configurations
const GITHUB_TOKEN = process.env.GITHUB_TOKEN; // PAT or installation token
const GITHUB_ORG = process.env.GITHUB_ORG || "purdue-rov";
const DEFAULT_REPO = process.env.DEFAULT_REPO || "board-template";

app.use(express.static(path.join(__dirname)));

app.get('/api/fetch-private-cad', async (req, res) => {
    const { filename, repo, sha } = req.query;
    const targetRepo = repo || DEFAULT_REPO;
    const ref = sha || "main";

    if (!filename) {
        return res.status(400).send("Bad Request: filename parameter is required.");
    }

    if (!GITHUB_TOKEN) {
        return res.status(500).send("Server Error: GITHUB_TOKEN environment variable is not defined.");
    }

    const githubUrl = `https://api.github.com/repos/${GITHUB_ORG}/${targetRepo}/contents/${filename}?ref=${ref}`;

    try {
        console.log(`Fetching private file from GitHub: ${githubUrl}`);
        const response = await axios.get(githubUrl, {
            headers: {
                'Authorization': `Bearer ${GITHUB_TOKEN}`,
                'Accept': 'application/vnd.github.v3.raw'
            },
            responseType: 'text'
        });

        // KiCad files are S-expressions (text/plain)
        res.setHeader('Content-Type', 'text/plain');
        res.status(200).send(response.data);
    } catch (error) {
        console.error(`Error fetching private file from GitHub:`, error.message);
        res.status(403).send("Unauthorized project asset retrieval attempt or file not found.");
    }
});

app.listen(PORT, () => {
    console.log(`Purdue ROV KiCanvas proxy server running on http://localhost:${PORT}`);
});
