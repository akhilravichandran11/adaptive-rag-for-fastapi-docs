// Every value from the response goes in through textContent or an attribute, never innerHTML:
// the answer and the sources come from untrusted excerpts and web pages.

const form = document.getElementById("ask-form");
const question = document.getElementById("question");
const ask = document.getElementById("ask");
const status = document.getElementById("status");
const error = document.getElementById("error");
const result = document.getElementById("result");

function showError(message) {
  error.textContent = message;
  error.hidden = false;
}

async function errorMessage(response) {
  const text = await response.text();
  if (response.status === 422) {
    try {
      const message = JSON.parse(text).detail?.[0]?.msg;
      if (message) return message;
    } catch {
      // Not FastAPI's validation body (a proxy's page, say): show the raw text below instead.
    }
  }
  return `Error ${response.status}: ${text}`;
}

function sourceItem(source) {
  const item = document.createElement("li");
  const origin = document.createElement("span");
  origin.className = "origin";
  origin.textContent = source.origin;
  item.append(origin);
  // Only an http(s) URL becomes a link, so a "javascript:" source cannot run.
  if (source.origin === "web" && source.source.startsWith("http")) {
    const link = document.createElement("a");
    link.setAttribute("href", source.source);
    link.setAttribute("rel", "noopener");
    link.textContent = source.source;
    item.append(link);
  } else {
    item.append(source.source);
  }
  return item;
}

function showResult(body) {
  document.getElementById("route").textContent = body.route;
  document.getElementById("answer").textContent = body.answer;

  const ranked = [...body.sources].sort((a, b) => a.rank - b.rank);
  document.getElementById("sources").replaceChildren(...ranked.map(sourceItem));

  const missing = document.getElementById("missing");
  missing.hidden = body.missing_aspects.length === 0;
  document.getElementById("missing-list").replaceChildren(
    ...body.missing_aspects.map((aspect) => {
      const item = document.createElement("li");
      item.textContent = aspect;
      return item;
    }),
  );
  result.hidden = false;
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  ask.disabled = true;
  status.hidden = false;
  error.hidden = true;
  result.hidden = true;
  try {
    const response = await fetch("/query", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: question.value }),
    });
    if (response.ok) {
      showResult(await response.json());
    } else {
      showError(await errorMessage(response));
    }
  } catch (failure) {
    showError(`Error: ${failure.message}`);
  } finally {
    // Reached on success, error and network failure alike, so the page never stays in "Working…".
    ask.disabled = false;
    status.hidden = true;
  }
});
