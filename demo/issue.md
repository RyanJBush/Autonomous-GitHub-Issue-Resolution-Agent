normalize_label keeps surrounding and repeated whitespace

normalize_label("  Bug   Report ") returns "  bug   report " but the docstring promises a
trimmed label with internal whitespace collapsed to a single space: "bug report".
