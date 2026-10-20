---
title: Diagrams
type: reference
status: active
date: 2026-10-15
tags: [obsidian]
---

# 🧬 System Architecture & Mermaid Diagrams

This file showcases how Obsidian natively compiles code-based text into dynamic visual flowcharts using the Mermaid framework.

---

## 🔄 User Authentication Pipeline
This flowchart tracks a user logging into a web application from the frontend UI down to database validation.

```mermaid
graph TD
    A[User Opens App] --> B{Is Session Active?}
    B -- Yes --> C[Redirect to Dashboard]
    B -- No --> D[Show Login Screen]
    D --> E[User Submits Credentials]
    E --> F{Validate via Backend API}
    F -- Token Match --> C
    F -- Invalid Pass --> G[Display Error Message]
    G --> D
```

---

## 🗓️ Agile Git Branching Strategy
This timeline represents a standard development workflow, tracking feature development, integration testing, and production deployment.

```mermaid
gitGraph
    commit id: "Initial Release"
    branch develop
    checkout develop
    commit id: "Setup staging env"
    branch feature-auth
    checkout feature-auth
    commit id: "Add JWT token verification"
    commit id: "Fix login UI margins"
    checkout develop
    merge feature-auth
    commit id: "Run automated test suites"
    checkout main
    merge develop tag: "v1.1.0"
```

---

## 🏛️ Database Relationship Framework (ER Diagram)
An Entity-Relationship map detailing the connections between customers, orders, and products.

```mermaid
erDiagram
    CUSTOMER ||--o{ ORDER : places
    ORDER ||--|{ LINE-ITEM : contains
    PRODUCT ||--o{ LINE-ITEM : ordered-in

    CUSTOMER {
        string id PK
        string name
        string email
    }
    ORDER {
        int order_number PK
        date date_placed
        string status
    }
    PRODUCT {
        string sku PK
        string title
        float price
    }
```

## Glossary

| Term | Meaning |
| --- | --- |
| **API** | Application Programming Interface: a defined way for software components to communicate. |
| **Branch** | An independent line of development in version control. |
| **Commit** | A recorded snapshot of changes in a version-control repository. |
| **Mermaid** | A text-based diagram syntax rendered by compatible Markdown tools. |
