import { expect, test } from "@playwright/test";
import path from "node:path";

const RESUME = path.resolve(__dirname, "../../fixtures/resumes/demo_jordan_lee.docx");

test("upload resume → review profile → rank fixture jobs → inspect evidence → save application → record OA deadline", async ({ page }) => {
  const email = `e2e-${Date.now()}@example.com`;

  // Register
  await page.goto("/login");
  await page.getByRole("tab", { name: "Create account" }).click();
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill("e2e-password-123");
  await page.getByRole("button", { name: "Create account" }).click();

  // Upload the resume (DOCX)
  await expect(page.getByRole("heading", { name: "Add your resume" })).toBeVisible();
  await page.locator("#resume-file").setInputFiles(RESUME);
  await page.getByRole("button", { name: "Read resume" }).click();

  // Review the extracted profile and correct one item
  await expect(page.getByRole("heading", { name: "Review version 1" })).toBeVisible();
  await expect(page.getByRole("button", { name: /Software Engineer Intern/ })).toBeVisible();
  await expect(page.getByText("Shown in work experience")).toBeVisible();
  await page.getByLabel("Add a skill you have").fill("Kubernetes");
  await page.getByRole("button", { name: "Add skill" }).click();
  await expect(page.getByText("Added by you").first()).toBeVisible();
  await page.getByRole("button", { name: "Confirm profile and continue" }).click();

  // Preferences onboarding
  await expect(page.getByRole("heading", { name: "Preferences" })).toBeVisible();
  await page.getByRole("button", { name: "Backend engineer" }).click();
  await page.getByRole("button", { name: "Save and see jobs" }).click();
  await expect(page.getByRole("heading", { name: "Jobs" })).toBeVisible();

  // No sources followed yet: follow the bundled demo fixtures
  await page.getByRole("link", { name: "Companies & sources" }).click();
  const fixtureRow = page.getByRole("listitem").filter({ hasText: "Demo fixtures" });
  await fixtureRow.getByRole("button", { name: "Follow" }).click();
  await expect(fixtureRow.getByRole("button", { name: "Unfollow" })).toBeVisible();

  // Ranked feed
  await page.getByRole("link", { name: "Jobs", exact: true }).click();
  await expect(page.getByText(/required shown/).first()).toBeVisible();
  await page.getByRole("link", { name: "Backend Software Engineer (Python)" }).click();

  // Evidence matrix
  await expect(page.getByRole("heading", { name: "Required qualifications" })).toBeVisible();
  const python = page.getByRole("listitem").filter({ hasText: "Strong Python skills" });
  await expect(python.getByText("Supported")).toBeVisible();
  await expect(python.locator(".quote").first()).toContainText("Python");
  await expect(page.getByRole("listitem").filter({ hasText: "Familiarity with Kafka" }).getByText("Not shown on resume")).toBeVisible();
  await expect(page.getByText("not a prediction of interviews")).toBeVisible();

  // Opening the employer link must not change tracking state
  await expect(page.getByRole("link", { name: /Open the employer's posting/ })).toHaveAttribute("target", "_blank");

  // Save and mark applied with the resume version
  await page.getByRole("button", { name: "I applied to this job" }).click();
  await expect(page.getByLabel("Resume version you submitted")).toBeVisible();
  await page.getByRole("button", { name: "Save as applied" }).click();
  await page.getByRole("link", { name: "Applied" }).click();

  // Record an OA deadline
  await expect(page.getByRole("heading", { name: "Backend Software Engineer (Python)" })).toBeVisible();
  await expect(page.getByText(/with resume version 1/)).toBeVisible();
  await page.getByLabel("Type").selectOption("oa");
  await page.getByLabel("Title").fill("HackerRank OA");
  await page.getByLabel("Due (local time)").fill("2030-11-20T23:59");
  await page.getByLabel("Time zone").selectOption("America/New_York");
  await page.getByRole("button", { name: "Add deadline" }).click();
  const deadline = page.getByRole("listitem").filter({ hasText: "HackerRank OA" });
  await expect(deadline).toBeVisible();
  await expect(deadline).toContainText("Online assessment");

  // History kept, and the deadline appears on the dashboard horizon only when due soon; check the tracker list instead.
  await page.getByRole("link", { name: "Applications" }).first().click();
  await expect(page.getByRole("row").filter({ hasText: "Backend Software Engineer (Python)" })).toContainText("HackerRank OA");
});
