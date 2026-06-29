module bind_wrapper;
    // This module exists solely to give sby a top-level entry point.
    // The actual binding happens via the bind statement below.
endmodule

module sva_checker (
    input wire en_i,
    input wire key_o,
    input wire clk_i,
    input wire rst_ni
);

assert property (@(posedge clk_i) disable iff (!rst_ni) (en_i == 1'b0) |-> (key_o == '0));

endmodule


bind keymgr_sideload_key sva_checker sva_checker_inst (.*);
