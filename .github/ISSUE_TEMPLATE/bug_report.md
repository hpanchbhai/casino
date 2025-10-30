name: Bug report
description: File a bug report
title: "[Bug]: "
labels: ["bug"]
assignees: []

body:
  - type: textarea
    id: summary
    attributes:
      label: Summary
      description: What happened? What did you expect?
    validations:
      required: true
  - type: textarea
    id: steps
    attributes:
      label: Steps to reproduce
      description: Include commands, inputs, screenshots, or logs
      placeholder: |
        1. …
        2. …
        3. …
  - type: input
    id: version
    attributes:
      label: Version / Commit
      placeholder: main / <commit SHA>
  - type: textarea
    id: logs
    attributes:
      label: Relevant logs
      render: shell
