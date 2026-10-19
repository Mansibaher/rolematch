// Captures screenshots of the running app (demo account) into docs/screenshots.
// Usage: node scripts/screenshots.mjs [baseURL] [outDir]
import { chromium } from "@playwright/test";
import path from "node:path";

const base = process.argv[2] ?? "http://localhost:3000";
const out = process.argv[3] ?? path.resolve("../docs/screenshots");
const executablePath = process.env.CHROMIUM_PATH || undefined;

const browser = await chromium.launch({ executablePath, args: ["--no-sandbox"] });
const page = await browser.newPage({ viewport: { width: 1360, height: 900 }, deviceScaleFactor: 1 });
await page.goto(`${base}/login`);
await page.screenshot({ path: `${out}/01-sign-in.png` });
await page.getByRole("button", { name: "Open the demo account" }).click();
await page.waitForURL(`${base}/`);
await page.getByRole("heading", { level: 1 }).waitFor();
await page.screenshot({ path: `${out}/02-today.png`, fullPage: true });
await page.goto(`${base}/jobs`);
await page.getByText("required shown").first().waitFor({ timeout: 60000 });
await page.screenshot({ path: `${out}/03-job-feed.png`, fullPage: false });
await page.getByRole("link", { name: "Backend Software Engineer (Python)" }).click();
await page.getByText("How this was scored").waitFor();
await page.screenshot({ path: `${out}/04-job-evidence.png`, fullPage: true });
await page.goto(`${base}/profile`);
await page.getByRole("link", { name: /Version 1/ }).first().click();
await page.getByText("Source text").waitFor();
await page.getByRole("button", { name: /Graduate Research Assistant/ }).click();
await page.screenshot({ path: `${out}/05-profile-review.png`, fullPage: false });
await page.goto(`${base}/applications`);
await page.getByRole("heading", { name: "In progress" }).waitFor();
await page.screenshot({ path: `${out}/06-applications.png` });
await page.goto(`${base}/sources`);
await page.getByText("Follow a company").waitFor();
await page.screenshot({ path: `${out}/07-sources.png` });
await page.goto(`${base}/preferences`);
await page.getByText("Work authorization").waitFor();
await page.screenshot({ path: `${out}/08-preferences.png` });
await page.setViewportSize({ width: 390, height: 844 });
await page.goto(`${base}/jobs`);
await page.getByText("required shown").first().waitFor({ timeout: 60000 });
await page.screenshot({ path: `${out}/09-mobile-feed.png` });
await browser.close();
console.log("screenshots written to", out);
