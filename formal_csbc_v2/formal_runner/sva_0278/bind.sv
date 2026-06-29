module bind_wrapper;
    // This module exists solely to give sby a top-level entry point.
    // The actual binding happens via the bind statement below.
endmodule

module sva_checker (
    input wire AdvRem,
    input wire IdRem,
    input wire GenRem,
    input wire clk_i,
    input wire rst_ni
);

assert property (@(posedge clk_i) disable iff (!rst_ni) (AdvRem % 8 == 0) && (IdRem % 8 == 0) && (GenRem % 8 == 0));

endmodule


bind keymgr_kmac_if sva_checker sva_checker_inst (.*);
