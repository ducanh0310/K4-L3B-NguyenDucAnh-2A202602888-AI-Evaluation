"""
Day 14 — AI Evaluation & Benchmarking Pipeline
AICB-P1: AI Practical Competency Program, Phase 1

Key concepts from lecture:
    - Evaluation = Scientific Method for AI (Hypothesis → Experiment → Measure → Conclude → Iterate)
    - 4 nhóm metrics: Task Completion, Answer Quality, RAG-Specific, Business
    - RAG pipeline metrics: Context Recall → Context Precision → Faithfulness → Answer Relevancy
    - LLM-as-Judge: rubric scoring 1-5, detect bias (positional, verbosity, self-preference)
    - Golden dataset: stratified sampling (5 Easy + 7 Medium + 5 Hard + 3 Adversarial)
    - Failure taxonomy: hallucination, irrelevant, incomplete, off_topic, refusal
    - 5 Whys method for root cause analysis
    - CI/CD integration: eval as quality gate (score < threshold = block deploy)
    - Continuous Improvement Loop: Evaluate → Analyze → Improve → Augment → Repeat

Instructions:
    1. Fill in every required section marked with TODO.
    2. Do NOT change class/function signatures. The optional ``contexts``
       parameter in ``run_full_eval`` is part of the required interface.
    3. Copy this file to solution/solution.py when done.
    4. Run: pytest tests/ -v

The reranking helper is an optional bonus exercise and may remain unimplemented.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable


# ---------------------------------------------------------------------------
# Task 1 — Data Models (Golden Dataset + Evaluation Results)
# ---------------------------------------------------------------------------

@dataclass
class QAPair:
    """
    A question-answer pair for evaluation (part of the Golden Dataset).

    From lecture: Golden dataset cần có:
        - question: câu hỏi user
        - ground_truth (expected_answer): expert-written expected answer
        - context: source documents cần retrieve
        - metadata: difficulty (easy/medium/hard), category, source_docs

    Fields:
        question:        The question to answer.
        expected_answer: The reference/ground-truth answer (expert-written).
        context:            Source context (may be empty string if not applicable).
        metadata:           Optional metadata dict (difficulty, category, etc.).
        retrieved_contexts: List of retrieved chunks (ORDER = retriever rank).
                            Used by the retrieval-side metrics (Task 2b).
    """
    question: str
    expected_answer: str
    context: str = ""
    metadata: dict = field(default_factory=dict)
    retrieved_contexts: list[str] = field(default_factory=list)


@dataclass
class EvalResult:
    """
    Evaluation result for a single Q&A pair.

    From lecture - RAG metrics pipeline:
        Question → Retriever → Context → Generator → Answer
        Each step has a metric: Context Recall, Context Precision, Faithfulness, Answer Relevancy

    From lecture - Score interpretation:
        0.8-1.0: Good (Monitor, maintain)
        0.6-0.8: Needs work (Analyze failures, iterate)
        < 0.6: Significant issues (Deep investigation required)

    Fields:
        qa_pair:        The original QAPair.
        actual_answer:  What the agent actually returned.
        faithfulness:   Float 0-1, how grounded the answer is in context.
        relevance:      Float 0-1, how relevant the answer is to the question.
        completeness:   Float 0-1, how complete the answer is vs expected.
        passed:         True if all three scores >= 0.5.
        failure_type:   None if passed, otherwise one of:
                        "hallucination", "irrelevant", "incomplete", "off_topic".
        context_precision: Float 0-1 or None — quality of retrieval ranking.
        context_recall:    Float 0-1 or None — coverage of expected by context.
                        (Both stay None unless retrieved chunks are supplied;
                         they are NOT part of overall_score().)
    """
    qa_pair: QAPair
    actual_answer: str
    faithfulness: float
    relevance: float
    completeness: float
    passed: bool
    failure_type: str | None = None
    context_precision: float | None = None
    context_recall: float | None = None

    def overall_score(self) -> float:
        """Compute the average of faithfulness, relevance, and completeness.

        Returns:
            (faithfulness + relevance + completeness) / 3.0
        """
        return (self.faithfulness + self.relevance + self.completeness) / 3.0


# ---------------------------------------------------------------------------
# Task 2 — RAGAS Evaluator (Simplified word-overlap heuristic)
# ---------------------------------------------------------------------------

STOPWORDS: set[str] = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "of", "in", "on", "at", "to", "for", "with", "as", "by", "and", "or",
    "it", "its", "this", "that", "these", "those", "from", "into", "than",
}


def _tokenize(text: str) -> set[str]:
    """Lowercase word tokenization, ignoring punctuation and stopwords."""
    if not text:
        return set()
    tokens = re.findall(r"\b\w+\b", text.lower())
    return {t for t in tokens if t not in STOPWORDS}


class RAGASEvaluator:
    """
    Evaluates RAG pipeline outputs using RAGAS-inspired heuristics.

    All metrics use word overlap rather than LLM calls for simplicity.
    Replace with actual LLM-based evaluation in production.
    """

    def evaluate_faithfulness(self, answer: str, context: str) -> float:
        """
        Measure how grounded the answer is in the context.
        """
        if not answer:
            return 1.0
        answer_tokens = _tokenize(answer)
        if not answer_tokens:
            return 1.0
        context_tokens = _tokenize(context)
        overlap = len(answer_tokens & context_tokens)
        score = overlap / len(answer_tokens)
        return max(0.0, min(1.0, float(score)))

    def evaluate_relevance(self, answer: str, question: str) -> float:
        """
        Measure how relevant the answer is to the question.
        """
        if not question:
            return 1.0
        question_tokens = _tokenize(question)
        if not question_tokens:
            return 1.0
        answer_tokens = _tokenize(answer)
        overlap = len(answer_tokens & question_tokens)
        score = overlap / len(question_tokens)
        return max(0.0, min(1.0, float(score)))

    def evaluate_completeness(self, answer: str, expected: str) -> float:
        """
        Measure how well the answer covers the expected answer.
        """
        if not expected:
            return 1.0
        expected_tokens = _tokenize(expected)
        if not expected_tokens:
            return 1.0
        answer_tokens = _tokenize(answer)
        overlap = len(answer_tokens & expected_tokens)
        score = overlap / len(expected_tokens)
        return max(0.0, min(1.0, float(score)))

    def evaluate_context_recall(self, contexts: list[str], expected: str) -> float:
        """Context Recall — how much of the expected answer is covered by the
        UNION of retrieved chunks.
        """
        if not expected:
            return 1.0
        expected_tokens = _tokenize(expected)
        if not expected_tokens:
            return 1.0
        union_tokens: set[str] = set()
        for chunk in contexts:
            union_tokens |= _tokenize(chunk)
        overlap = len(expected_tokens & union_tokens)
        score = overlap / len(expected_tokens)
        return max(0.0, min(1.0, float(score)))

    def evaluate_context_precision(
        self,
        contexts: list[str],
        expected: str,
        relevance_threshold: float = 0.1,
    ) -> float:
        """Context Precision — RANK-AWARE Average Precision (AP@K), like RAGAS.
        """
        if not expected:
            return 1.0
        if not contexts:
            return 0.0
        expected_tokens = _tokenize(expected)
        if not expected_tokens:
            return 1.0

        relevant_flags = []
        for chunk in contexts:
            chunk_tokens = _tokenize(chunk)
            cov = len(chunk_tokens & expected_tokens) / len(expected_tokens)
            relevant_flags.append(cov >= relevance_threshold)

        num_relevant = sum(relevant_flags)
        if num_relevant == 0:
            return 0.0

        sum_precision = 0.0
        running_rel = 0
        for k, is_rel in enumerate(relevant_flags, start=1):
            if is_rel:
                running_rel += 1
                precision_k = running_rel / k
                sum_precision += precision_k

        return max(0.0, min(1.0, float(sum_precision / num_relevant)))

    def run_full_eval(
        self,
        answer: str,
        question: str,
        context: str,
        expected: str,
        contexts: list[str] | None = None,
    ) -> EvalResult:
        """
        Run the three answer-side evaluations and, when ``contexts`` is
        supplied, both retrieval-side evaluations.
        """
        faithfulness = self.evaluate_faithfulness(answer, context)
        relevance = self.evaluate_relevance(answer, question)
        completeness = self.evaluate_completeness(answer, expected)

        passed = (faithfulness >= 0.5) and (relevance >= 0.5) and (completeness >= 0.5)

        failure_type = None
        if faithfulness < 0.3:
            failure_type = "hallucination"
        elif relevance < 0.3:
            failure_type = "irrelevant"
        elif completeness < 0.3:
            failure_type = "incomplete"
        elif not passed:
            failure_type = "off_topic"

        ctx_recall = None
        ctx_precision = None
        if contexts is not None:
            ctx_recall = self.evaluate_context_recall(contexts, expected)
            ctx_precision = self.evaluate_context_precision(contexts, expected)

        qa = QAPair(
            question=question,
            expected_answer=expected,
            context=context,
            retrieved_contexts=contexts if contexts is not None else [],
        )

        return EvalResult(
            qa_pair=qa,
            actual_answer=answer,
            faithfulness=faithfulness,
            relevance=relevance,
            completeness=completeness,
            passed=passed,
            failure_type=failure_type,
            context_precision=ctx_precision,
            context_recall=ctx_recall,
        )


def rerank_by_overlap(contexts: list[str], query: str) -> list[str]:
    """A minimal lexical reranker: sort chunks by word overlap with the query."""
    query_tokens = _tokenize(query)
    return sorted(contexts, key=lambda c: len(_tokenize(c) & query_tokens), reverse=True)


# ---------------------------------------------------------------------------
# Task 3 — LLM Judge
# ---------------------------------------------------------------------------

class LLMJudge:
    """
    Uses an LLM to score AI responses according to a rubric.
    """

    def __init__(self, judge_llm_fn: Callable[[str], str]) -> None:
        self.judge_llm_fn = judge_llm_fn

    def score_response(
        self,
        question: str,
        answer: str,
        rubric: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Score an AI response using the judge LLM.
        """
        prompt = f"Question: {question}\nAnswer: {answer}\nRubric: {rubric}\nRespond with JSON: {{'scores': {{...}}, 'reasoning': '...'}}"
        raw_res = self.judge_llm_fn(prompt)
        try:
            parsed = json.loads(raw_res)
            if isinstance(parsed, dict) and "scores" in parsed:
                reasoning = str(parsed.get("reasoning", raw_res))
                return {"scores": parsed["scores"], "reasoning": reasoning}
            elif isinstance(parsed, dict):
                scores = {k: float(v) for k, v in parsed.items() if isinstance(v, (int, float))}
                return {"scores": scores if scores else {k: 0.5 for k in rubric}, "reasoning": raw_res}
        except Exception:
            pass
        return {"scores": {k: 0.5 for k in rubric}, "reasoning": raw_res}

    def detect_bias(self, scores_batch: list[dict[str, Any]]) -> dict[str, Any]:
        """
        Detect potential bias patterns in a batch of judge scores.
        """
        all_scores = []
        for item in scores_batch:
            scores_dict = item.get("scores", {})
            all_scores.extend(scores_dict.values())

        avg_score = (sum(all_scores) / len(all_scores)) if all_scores else 0.5
        return {
            "positional_bias": False,
            "leniency_bias": avg_score > 0.8,
            "severity_bias": avg_score < 0.3,
        }


# ---------------------------------------------------------------------------
# Task 4 — Benchmark Runner
# ---------------------------------------------------------------------------

class BenchmarkRunner:
    """
    Runs a full evaluation benchmark.
    """

    def run(
        self,
        qa_pairs: list[QAPair],
        agent_fn: Callable[[str], str],
        evaluator: RAGASEvaluator,
    ) -> list[EvalResult]:
        """
        Run all QA pairs through the agent and evaluate each result.
        """
        results = []
        for pair in qa_pairs:
            ans = agent_fn(pair.question)
            res = evaluator.run_full_eval(
                answer=ans,
                question=pair.question,
                context=pair.context,
                expected=pair.expected_answer,
                contexts=pair.retrieved_contexts if pair.retrieved_contexts else None,
            )
            res.qa_pair = pair
            results.append(res)
        return results

    def generate_report(self, results: list[EvalResult]) -> dict[str, Any]:
        """
        Generate an aggregate report from evaluation results.
        """
        total = len(results)
        passed_count = sum(1 for r in results if r.passed)
        pass_rate = passed_count / total if total > 0 else 0.0

        avg_faithfulness = sum(r.faithfulness for r in results) / total if total > 0 else 0.0
        avg_relevance = sum(r.relevance for r in results) / total if total > 0 else 0.0
        avg_completeness = sum(r.completeness for r in results) / total if total > 0 else 0.0

        recalls = [r.context_recall for r in results if r.context_recall is not None]
        precisions = [r.context_precision for r in results if r.context_precision is not None]

        avg_context_recall = (sum(recalls) / len(recalls)) if recalls else None
        avg_context_precision = (sum(precisions) / len(precisions)) if precisions else None

        failure_types = {}
        for r in results:
            if r.failure_type:
                failure_types[r.failure_type] = failure_types.get(r.failure_type, 0) + 1

        return {
            "total": total,
            "passed": passed_count,
            "pass_rate": pass_rate,
            "avg_faithfulness": avg_faithfulness,
            "avg_relevance": avg_relevance,
            "avg_completeness": avg_completeness,
            "avg_context_recall": avg_context_recall,
            "avg_context_precision": avg_context_precision,
            "failure_types": failure_types,
        }

    def run_regression(self, new_results: list[EvalResult], baseline_results: list[EvalResult]) -> dict:
        """Compare new evaluation results against a baseline.
        """
        n_count = len(new_results)
        b_count = len(baseline_results)

        new_faith = sum(r.faithfulness for r in new_results) / n_count if n_count else 0.0
        new_rel = sum(r.relevance for r in new_results) / n_count if n_count else 0.0
        new_comp = sum(r.completeness for r in new_results) / n_count if n_count else 0.0

        base_faith = sum(r.faithfulness for r in baseline_results) / b_count if b_count else 0.0
        base_rel = sum(r.relevance for r in baseline_results) / b_count if b_count else 0.0
        base_comp = sum(r.completeness for r in baseline_results) / b_count if b_count else 0.0

        regressions = []
        if base_faith - new_faith > 0.05:
            regressions.append("faithfulness")
        if base_rel - new_rel > 0.05:
            regressions.append("relevance")
        if base_comp - new_comp > 0.05:
            regressions.append("completeness")

        return {
            "new_avg_faithfulness": new_faith,
            "new_avg_relevance": new_rel,
            "new_avg_completeness": new_comp,
            "baseline_avg_faithfulness": base_faith,
            "baseline_avg_relevance": base_rel,
            "baseline_avg_completeness": base_comp,
            "regressions": regressions,
            "passed": len(regressions) == 0,
        }

    def identify_failures(
        self,
        results: list[EvalResult],
        threshold: float = 0.5,
    ) -> list[EvalResult]:
        """
        Return EvalResults where any score is below threshold.
        """
        failures = []
        for r in results:
            if r.faithfulness < threshold or r.relevance < threshold or r.completeness < threshold:
                failures.append(r)
        return failures


# ---------------------------------------------------------------------------
# Task 5 — Failure Analyzer
# ---------------------------------------------------------------------------

class FailureAnalyzer:
    """
    Analyzes failed evaluation results to identify patterns and suggest fixes.
    """

    def categorize_failures(
        self, failures: list[EvalResult]
    ) -> dict[str, int]:
        """
        Count failures by failure_type.
        """
        categories: dict[str, int] = {}
        for f in failures:
            ftype = f.failure_type or "unknown"
            categories[ftype] = categories.get(ftype, 0) + 1
        return categories

    def find_root_cause(self, failure: EvalResult) -> str:
        """
        Suggest a root cause for a single failure based on its scores.
        """
        min_score = min(failure.faithfulness, failure.relevance, failure.completeness)
        if min_score == failure.faithfulness:
            return "Context is missing or irrelevant — improve retrieval"
        elif min_score == failure.relevance:
            return "Answer does not address the question — improve prompt clarity"
        elif min_score == failure.completeness:
            return "Answer is missing key information — increase context window or improve generation"
        else:
            return "Multiple issues detected — review full pipeline"

    def generate_improvement_log(self, failures: list[EvalResult], suggestions: list[str]) -> str:
        """Generate a Markdown table logging failures and improvement actions.
        """
        lines = [
            "| Failure ID | Type | Root Cause | Suggested Fix | Status |",
            "|------------|------|------------|---------------|--------|",
        ]
        for i, f in enumerate(failures):
            fid = f"F{i+1:03d}"
            ftype = f.failure_type or "Unknown"
            root_cause = self.find_root_cause(f)
            sug = suggestions[i] if i < len(suggestions) else (suggestions[0] if suggestions else "Review pipeline")
            lines.append(f"| {fid} | {ftype} | {root_cause} | {sug} | Open |")
        return "\n".join(lines)

    def generate_improvement_suggestions(
        self, failures: list[EvalResult]
    ) -> list[str]:
        """
        Generate a prioritized list of improvement suggestions based on failure patterns.
        """
        if not failures:
            return [
                "Increase chunk size in RAG pipeline to reduce context fragmentation",
                "Add few-shot examples showing complete answers to improve completeness",
                "Implement hallucination checker to filter unsupported claims",
            ]

        cats = self.categorize_failures(failures)
        suggs = []
        if cats.get("hallucination", 0) > 0:
            suggs.append("Implement hallucination checker to filter unsupported claims")
        if cats.get("incomplete", 0) > 0:
            suggs.append("Add few-shot examples showing complete answers to improve completeness")
        if cats.get("irrelevant", 0) > 0 or cats.get("off_topic", 0) > 0:
            suggs.append("Refine prompt instructions and add intent classification guardrails")

        defaults = [
            "Increase chunk size in RAG pipeline to reduce context fragmentation",
            "Improve retrieval ranker to bring relevant contexts to top",
            "Add context grounding validation before response generation",
        ]
        for d in defaults:
            if len(suggs) >= 3:
                break
            if d not in suggs:
                suggs.append(d)

        return suggs


# ---------------------------------------------------------------------------
# Entry point for manual testing
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    qa_pairs = [
        QAPair(
            question="What is RAG?",
            expected_answer="RAG stands for Retrieval-Augmented Generation, which combines retrieval with text generation.",
            context="RAG is a technique that retrieves relevant documents and uses them to ground LLM generation.",
            metadata={"difficulty": "easy", "category": "definition"},
        ),
        QAPair(
            question="What is the capital of France?",
            expected_answer="Paris is the capital of France.",
            context="France is a country in Western Europe. Its capital city is Paris.",
            metadata={"difficulty": "easy", "category": "factual"},
        ),
        QAPair(
            question="Explain backpropagation and why it matters for training",
            expected_answer="Backpropagation is an algorithm for training neural networks by computing gradients efficiently, enabling deep learning models to learn from errors.",
            context="Neural networks learn through gradient descent. Backpropagation efficiently computes these gradients layer by layer.",
            metadata={"difficulty": "medium", "category": "explanation"},
        ),
        QAPair(
            question="Should I use RAG or fine-tuning for my chatbot?",
            expected_answer="It depends on the use case: RAG is better for frequently updated knowledge, fine-tuning for consistent style/behavior. Consider cost, latency, and data freshness.",
            context="RAG retrieves external documents at inference time. Fine-tuning modifies model weights during training.",
            metadata={"difficulty": "hard", "category": "comparison"},
        ),
        QAPair(
            question="What is the meaning of life?",
            expected_answer="This question is outside the scope of this system. I can help with AI and technology questions.",
            context="This is an AI assistant specialized in technology topics.",
            metadata={"difficulty": "adversarial", "category": "out_of_scope"},
        ),
    ]

    evaluator = RAGASEvaluator()
    runner = BenchmarkRunner()

    def mock_agent(question: str) -> str:
        return f"Based on my knowledge: {question[:30]}... The answer involves key concepts."

    results = runner.run(qa_pairs, mock_agent, evaluator)
    report = runner.generate_report(results)
    print("=== Benchmark Report ===")
    for k, v in report.items():
        print(f"  {k}: {v}")

    failures = runner.identify_failures(results, threshold=0.5)
    print(f"\n=== Failures ({len(failures)}) ===")
    analyzer = FailureAnalyzer()

    categories = analyzer.categorize_failures(failures)
    print("Failure Categories:", categories)

    for f in failures:
        cause = analyzer.find_root_cause(f)
        print(f"  Root cause: {cause}")

    suggestions = analyzer.generate_improvement_suggestions(failures)
    print("\nImprovement Suggestions:")
    for s in suggestions:
        print(f"  - {s}")

    log = analyzer.generate_improvement_log(failures, suggestions)
    print("\n=== Improvement Log ===")
    print(log)
