function TutorialIcon({ name }) {
  if (name === "folder") {
    return (
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="M3.75 7.25h6l1.8 2h8.7v8.5a2 2 0 0 1-2 2H5.75a2 2 0 0 1-2-2V7.25Z" />
        <path d="M3.75 9.25V6.5a2 2 0 0 1 2-2h3.5l2 2h7a2 2 0 0 1 2 2v.75" />
      </svg>
    );
  }

  if (name === "questions") {
    return (
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="M5.5 4.5h13a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2v-11a2 2 0 0 1 2-2Z" />
        <path d="M8 8.5h8M8 12h8M8 15.5h5" />
      </svg>
    );
  }

  if (name === "download") {
    return (
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="M12 3.5v11M7.75 10.5 12 14.75l4.25-4.25M4.5 19.5h15" />
      </svg>
    );
  }

  if (name === "check") {
    return (
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="m7.5 12 3 3 6-7" />
        <circle cx="12" cy="12" r="9" />
      </svg>
    );
  }

  if (name === "chat") {
    return (
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="M5.5 5.75h13a2 2 0 0 1 2 2v7.5a2 2 0 0 1-2 2h-7.1l-4.5 3v-3H5.5a2 2 0 0 1-2-2v-7.5a2 2 0 0 1 2-2Z" />
        <path d="M7.5 9.5h9M7.5 13.25h6" />
      </svg>
    );
  }

  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M6.5 3.25h7l4 4v13.5h-11a2 2 0 0 1-2-2V5.25a2 2 0 0 1 2-2Z" />
      <path d="M13.5 3.25v4h4M8 12h6M8 15.5h4" />
    </svg>
  );
}

function ClickPath({ items, label }) {
  return (
    <div className="tutorial-click-path" aria-label={label}>
      {items.map((item, index) => (
        <span className="tutorial-click-part" key={`${item}-${index}`}>
          {index > 0 && <span className="tutorial-click-arrow" aria-hidden="true">→</span>}
          <span className="tutorial-click-target">{item}</span>
        </span>
      ))}
    </div>
  );
}

function GuideStep({ number, title, children, path, pathLabel, tone = "default" }) {
  return (
    <li className={`tutorial-step tutorial-step-${tone}`}>
      <span className="tutorial-step-number" aria-hidden="true">{number}</span>
      <div className="tutorial-step-copy">
        <h3>{title}</h3>
        <p>{children}</p>
        {path && <ClickPath items={path} label={pathLabel || path.join(", then ")} />}
      </div>
    </li>
  );
}

export function TutorialPage() {
  return (
    <article className="page tutorial-page">
      <header className="page-header tutorial-header">
        <div>
          <span className="page-kicker">Beginner guide</span>
          <h1>How to create a report</h1>
          <p>Follow these steps from top to bottom. You do not need technical experience.</p>
        </div>
      </header>

      <section className="tutorial-hero" aria-labelledby="tutorial-overview-title">
        <div className="tutorial-hero-copy">
          <span className="tutorial-hero-icon"><TutorialIcon name="file" /></span>
          <div>
            <span className="tutorial-badge"><span className="status-dot" /> Best first method</span>
            <h2 id="tutorial-overview-title">Turn one Telegram chat into a clear report</h2>
            <p>
              First, save the chat as a ZIP file. Then upload it, tell the app what questions to answer,
              and download the finished report.
            </p>
          </div>
        </div>
        <div className="tutorial-overview-flow" aria-label="Report creation overview">
          <span><TutorialIcon name="folder" />Prepare</span>
          <span className="tutorial-flow-arrow" aria-hidden="true">→</span>
          <span><TutorialIcon name="questions" />Ask</span>
          <span className="tutorial-flow-arrow" aria-hidden="true">→</span>
          <span><TutorialIcon name="check" />Wait</span>
          <span className="tutorial-flow-arrow" aria-hidden="true">→</span>
          <span><TutorialIcon name="download" />Download</span>
        </div>
      </section>

      <section className="tutorial-section" aria-labelledby="tutorial-source-title">
        <div className="tutorial-section-heading">
          <span className="tutorial-section-index">Start here</span>
          <div>
            <h2 id="tutorial-source-title">Choose how to provide the messages</h2>
            <p>A source is simply the chat content that the report will examine.</p>
          </div>
        </div>
        <div className="tutorial-choice-grid">
          <div className="tutorial-choice-card is-recommended">
            <span className="tutorial-choice-icon"><TutorialIcon name="folder" /></span>
            <div>
              <span className="tutorial-choice-label">Recommended for beginners</span>
              <h3>ZIP export</h3>
              <p>Save one chat with Telegram Desktop and upload that file. The detailed steps are below.</p>
            </div>
          </div>
          <div className="tutorial-choice-card">
            <span className="tutorial-choice-icon"><TutorialIcon name="chat" /></span>
            <div>
              <span className="tutorial-choice-label">If already configured</span>
              <h3>Collected chat</h3>
              <p>Use messages already synchronized by this workspace. A shorter guide is near the end.</p>
            </div>
          </div>
        </div>
      </section>

      <section className="tutorial-section" aria-labelledby="tutorial-prepare-title">
        <div className="tutorial-section-heading">
          <span className="tutorial-section-index">Part 1</span>
          <div>
            <h2 id="tutorial-prepare-title">Prepare a ZIP with Telegram Desktop</h2>
            <p>Use the installed computer application, not Telegram in a web browser or on a phone.</p>
          </div>
        </div>

        <ol className="tutorial-step-list">
          <GuideStep number="1" title="Open the chat you want to analyse">
            Start Telegram Desktop on your computer and select the group, channel, or conversation that should become a report.
          </GuideStep>
          <GuideStep
            number="2"
            title="Open the export window"
            path={["⋮", "Export chat history"]}
            pathLabel="Click the three-dot menu, then Export chat history"
          >
            Click the three dots in the top-right of that chat. In the menu, click “Export chat history”.
          </GuideStep>
          <GuideStep number="3" title="Choose what Telegram should save">
            Choose JSON if Telegram asks for a format; HTML also works. Select the date range and the photos, audio,
            video, or files you want the report to examine.
          </GuideStep>
          <GuideStep number="4" title="Export and wait for Telegram to finish" path={["Export"]}>
            Click “Export”. Keep Telegram Desktop open until it says the export is complete, then open the folder it created.
          </GuideStep>
          <GuideStep number="5" title="Compress the complete folder into one ZIP file" tone="important">
            Right-click the exported folder and choose “Compress”, “Create archive”, or “Send to compressed (zipped) folder”.
            Keep every exported file together. The finished filename must end in .zip.
          </GuideStep>
        </ol>

        <aside className="tutorial-callout tutorial-callout-tip">
          <span className="tutorial-callout-icon"><TutorialIcon name="check" /></span>
          <div>
            <strong>What you should have now</strong>
            <p>One ZIP file containing the exported chat. Do not upload an ordinary folder or only one file from inside it.</p>
          </div>
        </aside>
      </section>

      <section className="tutorial-section" aria-labelledby="tutorial-create-title">
        <div className="tutorial-section-heading">
          <span className="tutorial-section-index">Part 2</span>
          <div>
            <h2 id="tutorial-create-title">Create the report in this workspace</h2>
            <p>Return to this app and follow each click in order.</p>
          </div>
        </div>

        <ol className="tutorial-step-list">
          <GuideStep number="1" title="Open a new analysis" path={["New Analysis"]}>
            Click “New Analysis” in the menu on the left. This is the page where every new report begins.
          </GuideStep>
          <GuideStep
            number="2"
            title="Select your ZIP file"
            path={["ZIP export", "Browse", "Choose your .zip file"]}
            pathLabel="Click ZIP export, then Browse, then choose your ZIP file"
          >
            Choose “ZIP export”, click “Browse”, and select the ZIP you just made. The source area will show “Ready” when the file is accepted.
          </GuideStep>
          <GuideStep number="3" title="Write the questions for the report" path={["Define the report", "Add question"]}>
            Each question becomes one section in the finished report. Replace the example with what you need to know,
            such as “Which topics appear most often?” Add more questions only when they cover a different topic.
          </GuideStep>
          <GuideStep number="4" title="Check the optional processing settings" path={["Processing enhancements"]}>
            Leave “Analyse media” on if pictures, audio, or video matter. Turn on “Translate evidence” when messages
            need to be translated into English. Media can make the report take longer.
          </GuideStep>
          <GuideStep number="5" title="Start the analysis" path={["Start analysis"]} tone="important">
            When the summary says the source and questions are ready, click “Start analysis”. The app uploads the ZIP and opens the progress page automatically.
          </GuideStep>
          <GuideStep number="6" title="Wait for the report to finish">
            The Progress page shows each processing step. You may leave this page and return by selecting the analysis under “Recent analyses”.
            When it is done, the top of the page says “Report ready”.
          </GuideStep>
          <GuideStep
            number="7"
            title="Download and open the report"
            path={["Download all", "Unzip the download", "report", "index.html"]}
            pathLabel="Click Download all, unzip the download, open the report folder, then open index.html"
          >
            Click “Download all”. Unzip the downloaded file, open the report folder, and double-click index.html to read the report in your web browser.
          </GuideStep>
        </ol>
      </section>

      <section className="tutorial-section tutorial-alternative" aria-labelledby="tutorial-collected-title">
        <div className="tutorial-section-heading">
          <span className="tutorial-section-index">Alternative</span>
          <div>
            <h2 id="tutorial-collected-title">Use a collected chat instead</h2>
            <p>Use this route only when the chat already appears in this workspace.</p>
          </div>
        </div>
        <ClickPath
          items={["New Analysis", "Collected chat", "Choose chat", "Choose dates", "Write questions", "Start analysis"]}
          label="Click New Analysis, Collected chat, choose a chat, choose dates, write questions, then Start analysis"
        />
        <div className="tutorial-alternative-copy">
          <p>
            Choose the group or channel and a reporting period of 30 days or less. You can click “Use the last 30 days”
            for the quickest setup. Define the questions, start the analysis, wait for “Report ready”, and click “Download report”.
          </p>
          <p>
            If no chats are listed, open “Telegram Setup”. A Telegram account or external collector must be configured before this option can be used.
          </p>
        </div>
      </section>

      <section className="tutorial-section" aria-labelledby="tutorial-problems-title">
        <div className="tutorial-section-heading">
          <span className="tutorial-section-index">Need help?</span>
          <div>
            <h2 id="tutorial-problems-title">Common problems</h2>
            <p>Check these items if the Start analysis button is unavailable.</p>
          </div>
        </div>
        <div className="tutorial-problem-grid">
          <div><strong>The file is not accepted</strong><p>Select one file ending in .zip, not the uncompressed folder.</p></div>
          <div><strong>A question is incomplete</strong><p>Every visible question needs text. Remove any question you do not want to use.</p></div>
          <div><strong>No collected chats appear</strong><p>The chat must first be connected and synchronized in Telegram Setup.</p></div>
          <div><strong>The date range is rejected</strong><p>Make sure “From” is earlier than “To” and the range is no longer than 30 days.</p></div>
          <div><strong>Processing takes a long time</strong><p>Large exports and media analysis need more time. Check Recent analyses for the current status.</p></div>
        </div>
      </section>
    </article>
  );
}
