# Minimal JSON scalar/query reader for the installation-context bootstrap.
#
# Query paths are separated by ASCII FS (034). Modes: get, type, len, keys.

function fail(message) {
    parse_failed = 1
    print "json-query: " message > "/dev/stderr"
    exit 2
}

function skip_ws() {
    while (position <= length(document) && substr(document, position, 1) ~ /[ \t\r\n]/) {
        position++
    }
}

function hex_value(character) {
    character = tolower(character)
    if (character >= "0" && character <= "9") {
        return character + 0
    }
    return index("abcdef", character) + 9
}

function byte_value(character, candidate) {
    for (candidate = 1; candidate < 256; candidate++) {
        if (sprintf("%c", candidate) == character) {
            return candidate
        }
    }
    return 0
}

function continuation(position_value, value) {
    if (position_value > length(document)) {
        return -1
    }
    value = byte_value(substr(document, position_value, 1))
    if (value < 128 || value > 191) {
        return -1
    }
    return value
}

function validate_utf8(index_value, first, second, third, fourth) {
    if (length(document) >= 3 && byte_value(substr(document, 1, 1)) == 239 && byte_value(substr(document, 2, 1)) == 187 && byte_value(substr(document, 3, 1)) == 191) {
        fail("UTF-8 BOM is not allowed")
    }
    index_value = 1
    while (index_value <= length(document)) {
        first = byte_value(substr(document, index_value, 1))
        if (first <= 127) {
            index_value++
            continue
        }
        second = continuation(index_value + 1)
        if (first >= 194 && first <= 223 && second >= 128) {
            index_value += 2
            continue
        }
        third = continuation(index_value + 2)
        if (first >= 224 && first <= 239 && second >= 128 && third >= 128 && !(first == 224 && second < 160) && !(first == 237 && second > 159)) {
            index_value += 3
            continue
        }
        fourth = continuation(index_value + 3)
        if (first >= 240 && first <= 244 && second >= 128 && third >= 128 && fourth >= 128 && !(first == 240 && second < 144) && !(first == 244 && second > 143)) {
            index_value += 4
            continue
        }
        fail("invalid UTF-8")
    }
}

function hex_number(text, result, index_value) {
    result = 0
    for (index_value = 1; index_value <= length(text); index_value++) {
        result = (result * 16) + hex_value(substr(text, index_value, 1))
    }
    return result
}

function utf8(codepoint) {
    if (codepoint <= 127) {
        return sprintf("%c", codepoint)
    }
    if (codepoint <= 2047) {
        return sprintf("%c%c", 192 + int(codepoint / 64), 128 + (codepoint % 64))
    }
    if (codepoint <= 65535) {
        return sprintf("%c%c%c", 224 + int(codepoint / 4096), 128 + (int(codepoint / 64) % 64), 128 + (codepoint % 64))
    }
    return sprintf("%c%c%c%c", 240 + int(codepoint / 262144), 128 + (int(codepoint / 4096) % 64), 128 + (int(codepoint / 64) % 64), 128 + (codepoint % 64))
}

function parse_string(quote, result, character, escape, hex, codepoint, low_hex, low) {
    quote = substr(document, position, 1)
    if (quote != "\"") {
        fail("expected string at byte " position)
    }
    position++
    result = ""
    while (position <= length(document)) {
        character = substr(document, position, 1)
        position++
        if (character == "\"") {
            parsed_string = result
            return
        }
        if (character != "\\") {
            if (byte_value(character) < 32) {
                fail("unescaped control character")
            }
            result = result character
            continue
        }
        if (position > length(document)) {
            fail("unterminated escape")
        }
        escape = substr(document, position, 1)
        position++
        if (escape == "\"" || escape == "\\" || escape == "/") {
            result = result escape
        } else if (escape == "b") {
            result = result sprintf("%c", 8)
        } else if (escape == "f") {
            result = result sprintf("%c", 12)
        } else if (escape == "n") {
            result = result "\n"
        } else if (escape == "r") {
            result = result "\r"
        } else if (escape == "t") {
            result = result "\t"
        } else if (escape == "u") {
            hex = substr(document, position, 4)
            if (length(hex) != 4 || hex !~ /^[0-9A-Fa-f]{4}$/) {
                fail("invalid unicode escape")
            }
            position += 4
            codepoint = hex_number(hex)
            if (codepoint >= 55296 && codepoint <= 56319) {
                if (substr(document, position, 2) != "\\u") {
                    fail("unpaired high surrogate")
                }
                position += 2
                low_hex = substr(document, position, 4)
                if (length(low_hex) != 4 || low_hex !~ /^[0-9A-Fa-f]{4}$/) {
                    fail("invalid low surrogate")
                }
                position += 4
                low = hex_number(low_hex)
                if (low < 56320 || low > 57343) {
                    fail("invalid low surrogate")
                }
                codepoint = 65536 + ((codepoint - 55296) * 1024) + (low - 56320)
            } else if (codepoint >= 56320 && codepoint <= 57343) {
                fail("unpaired low surrogate")
            }
            result = result utf8(codepoint)
        } else {
            fail("invalid string escape")
        }
    }
    fail("unterminated string")
}

function child_path(parent, child) {
    return parent == "" ? child : parent separator child
}

function has_case_variant(path, components, count_value, parent, exact, candidate, index_value, key_index) {
    count_value = split(path, components, separator)
    parent = ""
    for (index_value = 1; index_value <= count_value; index_value++) {
        exact = child_path(parent, components[index_value])
        if (exact in value_type) {
            parent = exact
            continue
        }
        if (value_type[parent] == "object") {
            for (key_index = 0; key_index < value_length[parent]; key_index++) {
                candidate = object_key[child_path(parent, key_index)]
                if (tolower(candidate) == tolower(components[index_value])) {
                    return 1
                }
            }
        }
        return 0
    }
    return 0
}

function parse_object(path, key, child, count_value, key_index, prior_key) {
    value_type[path] = "object"
    position++
    skip_ws()
    count_value = 0
    if (substr(document, position, 1) == "}") {
        position++
        value_length[path] = 0
        return
    }
    while (1) {
        parse_string()
        key = parsed_string
        if (index(key, separator) != 0) {
            fail("object key contains the reserved query separator")
        }
        for (key_index = 0; key_index < count_value; key_index++) {
            prior_key = object_key[child_path(path, key_index)]
            if (prior_key != key && tolower(prior_key) == tolower(key)) {
                fail("object keys differ only by case")
            }
        }
        skip_ws()
        if (substr(document, position, 1) != ":") {
            fail("expected colon at byte " position)
        }
        position++
        child = child_path(path, key)
        if (child in value_type) {
            fail("duplicate object key '" key "'")
        }
        object_key[child_path(path, count_value)] = key
        parse_value(child)
        count_value++
        skip_ws()
        if (substr(document, position, 1) == "}") {
            position++
            value_length[path] = count_value
            return
        }
        if (substr(document, position, 1) != ",") {
            fail("expected comma at byte " position)
        }
        position++
        skip_ws()
    }
}

function parse_array(path, child, count_value) {
    value_type[path] = "array"
    position++
    skip_ws()
    count_value = 0
    if (substr(document, position, 1) == "]") {
        position++
        value_length[path] = 0
        return
    }
    while (1) {
        child = child_path(path, count_value)
        parse_value(child)
        count_value++
        skip_ws()
        if (substr(document, position, 1) == "]") {
            position++
            value_length[path] = count_value
            return
        }
        if (substr(document, position, 1) != ",") {
            fail("expected comma at byte " position)
        }
        position++
        skip_ws()
    }
}

function parse_number(path, start, token) {
    start = position
    if (substr(document, position, 1) == "-") {
        position++
    }
    if (substr(document, position, 1) == "0") {
        position++
    } else {
        if (substr(document, position, 1) !~ /[1-9]/) {
            fail("invalid number at byte " position)
        }
        while (substr(document, position, 1) ~ /[0-9]/) {
            position++
        }
    }
    if (substr(document, position, 1) == ".") {
        position++
        if (substr(document, position, 1) !~ /[0-9]/) {
            fail("invalid fraction")
        }
        while (substr(document, position, 1) ~ /[0-9]/) {
            position++
        }
    }
    if (substr(document, position, 1) ~ /[eE]/) {
        position++
        if (substr(document, position, 1) ~ /[+-]/) {
            position++
        }
        if (substr(document, position, 1) !~ /[0-9]/) {
            fail("invalid exponent")
        }
        while (substr(document, position, 1) ~ /[0-9]/) {
            position++
        }
    }
    token = substr(document, start, position - start)
    value_type[path] = "number"
    value_scalar[path] = token
}

function parse_value(path, character) {
    skip_ws()
    character = substr(document, position, 1)
    if (character == "{") {
        parse_object(path)
    } else if (character == "[") {
        parse_array(path)
    } else if (character == "\"") {
        parse_string()
        value_type[path] = "string"
        value_scalar[path] = parsed_string
    } else if (character == "-" || character ~ /[0-9]/) {
        parse_number(path)
    } else if (substr(document, position, 4) == "true") {
        value_type[path] = "boolean"
        value_scalar[path] = "true"
        position += 4
    } else if (substr(document, position, 5) == "false") {
        value_type[path] = "boolean"
        value_scalar[path] = "false"
        position += 5
    } else if (substr(document, position, 4) == "null") {
        value_type[path] = "null"
        value_scalar[path] = ""
        position += 4
    } else {
        fail("unexpected value at byte " position)
    }
}

BEGIN {
    separator = sprintf("%c", 28)
    position = 1
}

{
    document = document $0 "\n"
}

END {
    if (parse_failed) {
        exit 2
    }
    validate_utf8()
    parse_value("")
    skip_ws()
    if (position <= length(document)) {
        fail("trailing data at byte " position)
    }
    if (!(query_path in value_type)) {
        if (has_case_variant(query_path)) {
            exit 5
        }
        exit 3
    }
    if (mode == "type") {
        print value_type[query_path]
    } else if (mode == "len") {
        if (value_type[query_path] != "array" && value_type[query_path] != "object") {
            exit 4
        }
        print value_length[query_path]
    } else if (mode == "keys") {
        if (value_type[query_path] != "object") {
            exit 4
        }
        for (index_value = 0; index_value < value_length[query_path]; index_value++) {
            print object_key[child_path(query_path, index_value)]
        }
    } else if (mode == "get" || mode == "hex") {
        if (value_type[query_path] != "string" && value_type[query_path] != "number" && value_type[query_path] != "boolean" && value_type[query_path] != "null") {
            exit 4
        }
        if (mode == "get") {
            printf "%s", value_scalar[query_path]
        } else {
            for (index_value = 1; index_value <= length(value_scalar[query_path]); index_value++) {
                printf "%02x", byte_value(substr(value_scalar[query_path], index_value, 1))
            }
        }
    } else {
        fail("unknown mode '" mode "'")
    }
}
