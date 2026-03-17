class CompilerError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__("Compiler error: " + message)

class ParserError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__("Parser error: " + message)

class VerificationError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__("Verification error: " + message)

class IRRuntimeError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__("IR runtime error: " + message)

class IRParseError(Exception):
    pass