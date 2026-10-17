const express = require('express');
const cors = require('cors');
const { GoogleGenerativeAI } = require('@google/generative-ai');
const q = require('node-q');

const app = express();
app.use(cors());
app.use(express.json());

// Set up Gemini AI
// You can paste your API key here or use process.env.GEMINI_API_KEY
const apiKey = process.env.GEMINI_API_KEY || "paste_your_api_key_here";
const genAI = new GoogleGenerativeAI(apiKey);
const model = genAI.getGenerativeModel({ model: "gemini-1.5-flash" });

const SCHEMA_PROMPT = `
You are an expert KDB+ / q database programmer.
I have a table named \`greeks_chain\` running on port 5000 with the following schema:
- sym: symbol (e.g., \`NIFTY26MAY24500CE)
- underlying: symbol (e.g., \`NIFTY)
- expiry: date
- strike: float
- type: symbol (\`CE or \`PE)
- ltp: float
- iv: float (implied volatility in percentage)
- delta: float
- theta: float
- gamma: float
- vega: float
- oi: long (open interest)

Translate the user's natural language request into a valid, optimized q-SQL query.
- Use 'asc' or 'desc' for sorting.
- Use '#' for limiting rows (e.g., \`5#select...\` for top 5).
- Use 'like' for string matching if needed.

Return ONLY the raw q query string. Do NOT include markdown formatting. Do NOT explain yourself.

User Request: "{text}"
`;

app.post('/api/generate-q', async (req, res) => {
    try {
        const prompt = SCHEMA_PROMPT.replace("{text}", req.body.text);
        const result = await model.generateContent(prompt);
        
        // Clean up markdown backticks if Gemini includes them
        let rawQuery = result.response.text().replace(/^```q?/, '').replace(/```$/, '').trim();
        res.json({ query: rawQuery });
    } catch (e) {
        res.status(500).json({ detail: "LLM Generation Error: " + e.message });
    }
});

app.post('/api/execute-q', (req, res) => {
    q.connect({ host: 'localhost', port: 5000 }, (err, con) => {
        if (err) return res.status(500).json({ detail: "KDB Connection Error: " + err.message });
        
        con.k(req.body.query, (err, result) => {
            con.close();
            if (err) return res.status(400).json({ detail: "KDB Execution Error: " + err });
            
            // node-q naturally converts KDB tables into clean arrays of JSON objects!
            let data = Array.isArray(result) ? result : [{ result: result }];
            res.json({ data });
        });
    });
});

app.listen(8000, () => console.log('Node.js AI Backend running on port 8000!'));